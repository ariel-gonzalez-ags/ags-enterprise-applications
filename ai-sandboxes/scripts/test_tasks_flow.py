#!/usr/bin/env python3
"""End-to-end product API test: task lifecycle with the planner (Gemini)
mocked at the planner module boundary.

Usage: python3 scripts/test_tasks_flow.py

Verifies: auth gate → create → chat (mock plan) → state flips to planned →
approve → simulated run ticks → verified with artifacts → ownership scoping.
"""
import asyncio
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, "api")

# Point the DB at a temp file before the app imports db/config.
_tmpdir = tempfile.mkdtemp(prefix="ags-test-")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "test.db")

from httpx import ASGITransport, AsyncClient
from starlette.responses import Response

from app import db, session
from app.config import load
from app.main import app

MOCK_PLAN = {
    "reply": "Plan ready: provision on Azure, harden with Ansible, verify with bash.",
    "title": "Harden Ubuntu to CIS L2",
    "plan": {
        "summary": "Provision, harden, verify.",
        "clouds": ["azure"],
        "deliverables": [
            {"id": "ansible", "why": "apply hardening"},
            {"id": "bash", "why": "verify compliance"},
        ],
        "est_hours": 3,
    },
}


def _signed_cookie(settings, sub="user-1"):
    resp = Response()
    session.create_session(resp, settings,
                           {"sub": sub, "email": f"{sub}@ex.com", "name": sub, "picture": ""})
    return resp.headers["set-cookie"].split(";")[0].split("=", 1)[1]


async def main():
    db.init(os.environ["DB_PATH"])
    await db.create_schema()
    settings = load()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # 1. unauthenticated → 401
        r = await c.get("/api/tasks")
        assert r.status_code == 401, r.status_code
        print("ok    tasks require auth")

        c.cookies.set("ags_session", _signed_cookie(settings))

        # 2. create
        r = await c.post("/api/tasks", json={"title": ""})
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        assert len(tid) == 36 and tid.count("-") == 4, f"not a GUID: {tid}"
        assert r.json()["state"] == "drafting"
        print("ok    task created in drafting:", tid)

        # 3. chat without planner key: accepted, degradation lands in-thread
        with mock.patch("app.routers.tasks.planner.reply",
                        side_effect=__import__("app.planner", fromlist=["PlannerUnavailable"]).PlannerUnavailable):
            r = await c.post(f"/api/tasks/{tid}/messages", json={"text": "hi"})
            assert r.status_code == 202, r.text
            from app.routers.tasks import _plan_in_background
            await _plan_in_background(tid, app.state.settings)
        r = await c.get(f"/api/tasks/{tid}")
        msgs = r.json()["messages"]
        assert "isn't configured" in msgs[-1]["text"], msgs[-1]["text"]
        assert r.json()["agent_pending"] is False
        print("ok    missing planner key degrades into an in-thread message")

        # 4. chat with mocked planner → 202 instantly, reply lands async
        with mock.patch("app.routers.tasks.planner.reply",
                        new=mock.AsyncMock(return_value=MOCK_PLAN)):
            r = await c.post(f"/api/tasks/{tid}/messages",
                             json={"text": "Harden my Ubuntu VMs to CIS L2"})
            assert r.status_code == 202, r.text
            assert r.json()["accepted"] is True
            # run the background planner deterministically
            from app.routers.tasks import _plan_in_background
            await _plan_in_background(tid, app.state.settings)

        r = await c.get(f"/api/tasks/{tid}")
        t = r.json()
        assert t["state"] == "planned", t["state"]
        assert t["title"] == "Harden Ubuntu to CIS L2"
        assert t["provider"] == "azure" and "ansible" in t["formats"]
        assert t["config"]["maxHours"] == 3
        assert t["agent_pending"] is False
        agent_msgs = [m for m in t["messages"] if m["role"] == "agent"]
        assert agent_msgs and agent_msgs[-1]["plan"]["deliverables"][0]["id"] == "ansible"
        print("ok    async planner: 202 accepted, plan landed, task planned")

        # 5. approve → running, then simulated run lands at verified.
        # Spawn is mocked out so the test drives the run deterministically.
        with mock.patch("app.routers.tasks.runner.spawn"):
            r = await c.post(f"/api/tasks/{tid}/approve")
        assert r.status_code == 200 and r.json()["state"] == "running"
        print("ok    approve flips to running")

        # 5a. Kill switch (TODO #7): a running task accepts an abort request. Use a
        # DEDICATED task so the abort flag never leaks into the driven run below.
        from app import killswitch as _runner
        from app.models import Task
        r = await c.post("/api/tasks", json={"title": "killme", "provider": "azure"})
        kill_tid = r.json()["id"]
        async with db.session() as s:
            t = await s.get(Task, kill_tid); t.state = "running"; await s.commit()
        r = await c.post(f"/api/tasks/{kill_tid}/abort")
        assert r.status_code == 200 and r.json()["state"] == "aborting", r.text
        assert _runner.is_aborted(kill_tid), "abort flag not set"
        _runner.clear(kill_tid)  # leave no residue for other tests
        print("ok    abort endpoint: running task accepts a kill request")

        # abort is owner-scoped: an unknown/foreign task id gets 404, not 403
        r = await c.post("/api/tasks/does-not-exist/abort")
        assert r.status_code == 404, r.text
        print("ok    abort endpoint: unknown/foreign task -> 404")

        # a non-running task cannot be aborted (409)
        r = await c.post("/api/tasks", json={"title": "draft", "provider": "azure"})
        draft_tid = r.json()["id"]
        r = await c.post(f"/api/tasks/{draft_tid}/abort")
        assert r.status_code == 409, r.text
        print("ok    abort endpoint: non-running task -> 409")

        # 5b. Ember gate: a user whose balance is drained cannot approve a run.
        from app import embers
        from app.config import load as _load
        from app.models import Task
        sub = (await c.get("/api/auth/me")).json()["user"]["sub"]
        await embers.burn(sub, "drain", await embers.balance(sub, _load()), 1, 1)
        r = await c.post("/api/tasks", json={"title": "broke", "provider": "azure"})
        brok_tid = r.json()["id"]
        async with db.session() as s:
            t = await s.get(Task, brok_tid); t.state = "planned"; await s.commit()
        r = await c.post(f"/api/tasks/{brok_tid}/approve")
        assert r.status_code == 402 and "Embers" in r.json()["detail"], r.text
        # restore a healthy balance so later approvals in this session still work
        async with db.session() as s:
            acct = await s.get(embers.EmberAccount, sub)
            acct.balance = 10000; await s.commit()
        print("ok    ember gate: insufficient balance refuses approve (402)")

        # 5c. Rate limits (TODO #6): a user at the concurrent-sandbox cap cannot
        # approve another run (429). Count how many are already running (earlier
        # steps leave some), top up to the cap, then one more approve is refused.
        from app import ratelimit as _rl
        from app.config import load as _load2
        from sqlalchemy import select, func
        cap = _load2().ratelimit_max_concurrent
        assert cap == 2
        async with db.session() as s:
            running_now = (await s.execute(
                select(func.count()).select_from(Task)
                .where(Task.owner_sub == sub, Task.state == "running"))).scalar() or 0
        made = []
        for i in range(max(0, cap - running_now)):
            rr = await c.post("/api/tasks", json={"title": f"rl{i}", "provider": "azure"})
            rid = rr.json()["id"]
            async with db.session() as s:
                t = await s.get(Task, rid); t.state = "running"; await s.commit()
            made.append(rid)
        # now at the cap: a fresh approve is refused 429
        rr = await c.post("/api/tasks", json={"title": "rl-over", "provider": "azure"})
        over_tid = rr.json()["id"]
        async with db.session() as s:
            t = await s.get(Task, over_tid); t.state = "planned"; await s.commit()
        r = await c.post(f"/api/tasks/{over_tid}/approve")
        assert r.status_code == 429 and "running" in r.json()["detail"].lower(), r.text
        # freeing a slot (one finishes) lets it through again
        async with db.session() as s:
            t = await s.get(Task, kill_tid); t.state = "verified"; await s.commit()
        with mock.patch("app.routers.tasks.runner.spawn"):
            r = await c.post(f"/api/tasks/{over_tid}/approve")
        assert r.status_code == 200, r.text
        # clean up: return leftover running tasks to planned so later checks pass
        async with db.session() as s:
            for rid in made + [over_tid]:
                t = await s.get(Task, rid)
                if t and t.state == "running": t.state = "planned"
            await s.commit()
        print("ok    rate limits: concurrent cap refuses a run over the cap (429), frees on finish")

        import app.runner as runner
        runner._TICK_SECONDS = 0  # speed up the simulation
        await runner.run_task(tid)  # run synchronously for the test

        r = await c.get(f"/api/tasks/{tid}")
        t = r.json()
        assert t["state"] == "verified", t["state"]
        assert t["checks"] == {"passed": t["checks"]["total"], "total": t["checks"]["total"]}
        kinds = {a["kind"] for a in t["artifacts"]}
        assert "ansible" in kinds and "markdown" in kinds, kinds
        print(f"ok    simulated run verified: {t['checks']['total']}/{t['checks']['total']} checks, {len(t['artifacts'])} artifacts")

        # 5b. artifacts are fetchable, owner-scoped, attachment-marked
        art = next(a for a in t["artifacts"] if a["id"] == "harden.yml")
        r = await c.get(art["url"])
        assert r.status_code == 200, r.status_code
        assert "attachment" in r.headers.get("content-disposition", "")
        assert tid in r.text  # simulated content embeds the task id
        r = await c.get(f"/api/tasks/{tid}/artifacts/nope.txt")
        assert r.status_code == 404
        print("ok    artifact download: 200 + attachment + content; unknown 404")

        # 6. chat closed after planning+run
        r = await c.post(f"/api/tasks/{tid}/messages", json={"text": "more?"})
        assert r.status_code == 409, r.status_code
        print("ok    chat rejected once task is verified")

        # 6a. deliverable set is user-editable while shapeable
        r = await c.patch(f"/api/tasks/{tid}", json={"formats": ["ansible"]})
        assert r.status_code == 409, r.status_code  # verified: locked
        r2 = await c.post("/api/tasks", json={"title": "scratch"})
        tid2 = r2.json()["id"]
        r = await c.patch(f"/api/tasks/{tid2}", json={"formats": ["ansible", "Helm Chart!!"]})
        assert r.status_code == 200 and r.json()["formats"] == ["ansible", "helm-chart"]
        r = await c.patch(f"/api/tasks/{tid2}", json={"formats": []})
        assert r.status_code == 422
        print("ok    PATCH formats: editable in draft, slugged, empty rejected, locked after")

        # 6c. every accepted format yields an artifact, including custom ones
        r = await c.post("/api/tasks", json={"title": "custom fmt"})
        tid3 = r.json()["id"]
        # slug the way the user types it: multi-word, mixed case
        await c.patch(f"/api/tasks/{tid3}", json={"formats": ["ansible", "JSON Policy Format!!"]})
        fmts = (await c.get(f"/api/tasks/{tid3}")).json()["formats"]
        assert "json-policy-format" in fmts, fmts
        # simulate the planner having planned it (approve requires `planned`)
        import app.db as _db
        from app.models import Task as _Task
        async with _db.session() as s:
            t3 = await s.get(_Task, tid3)
            t3.state = "planned"
            await s.commit()
        r = await c.post(f"/api/tasks/{tid3}/approve")
        assert r.status_code == 200, r.text
        await runner.run_task(tid3)
        t3 = (await c.get(f"/api/tasks/{tid3}")).json()
        ids = {a["id"] for a in t3["artifacts"]}
        assert "harden.yml" in ids, ids
        assert "json-policy-format.json" in ids, ids  # extension inferred from token
        assert "runbook.md" in ids and "verify.sh" in ids
        print("ok    custom format slugged (json-policy-format) -> .json artifact")

        # 6d. delete: drafts and verified go; only a running task is refused
        r = await c.delete(f"/api/tasks/{tid2}")
        assert r.status_code == 204, r.status_code
        r = await c.get(f"/api/tasks/{tid2}")
        assert r.status_code == 404
        # tid3 is verified (from 6c): deletable now. (tid stays: used below.)
        r = await c.delete(f"/api/tasks/{tid3}")
        assert r.status_code == 204, r.status_code
        # a running task refuses
        r = await c.post("/api/tasks", json={"title": "runner"})
        tidr = r.json()["id"]
        import app.db as _db2
        from app.models import Task as _Task2
        async with _db2.session() as s:
            tr = await s.get(_Task2, tidr); tr.state = "running"; await s.commit()
        r = await c.delete(f"/api/tasks/{tidr}")
        assert r.status_code == 409, r.status_code
        print("ok    delete: draft+verified removed (204), running refused (409)")

        # 6e. provider: set at create, patchable while shapeable, validated
        r = await c.post("/api/tasks", json={"title": "pv", "provider": "gcp"})
        assert r.json()["provider"] == "gcp", r.json()
        tidp = r.json()["id"]
        r = await c.patch(f"/api/tasks/{tidp}", json={"provider": "aws"})
        assert r.json()["provider"] == "aws"
        r = await c.patch(f"/api/tasks/{tidp}", json={"provider": "oracle"})
        assert r.status_code == 422, r.status_code
        r = await c.post("/api/tasks", json={"title": "bad", "provider": "oracle"})
        assert r.status_code == 422
        print("ok    provider: set at create, patched, invalid rejected (422)")

        # 6h. guarantee toggles: idempotent / destroy_after / max_hours PATCHable
        r = await c.post("/api/tasks", json={"title": "guarantees"})
        tidg = r.json()["id"]
        r = await c.patch(f"/api/tasks/{tidg}", json={"idempotent": False, "destroy_after": False, "max_hours": 8})
        assert r.status_code == 200, r.text
        assert r.json()["idempotent"] is False
        assert r.json()["config"]["destroyAfter"] is False and r.json()["config"]["maxHours"] == 8
        r = await c.patch(f"/api/tasks/{tidg}", json={"max_hours": 99})  # out of range
        assert r.status_code == 422, r.status_code
        print("ok    guarantees: idempotent/destroy_after/max_hours PATCHable, range-validated")

        # 6g. model: selectable at create, patchable while shapeable, validated
        r = await c.get("/api/models")
        ids = {m["id"] for m in r.json()["models"]}
        assert "gemini-3.6-flash" in ids and len(ids) >= 2, ids
        r = await c.post("/api/tasks", json={"title": "mdl", "model": "gemini-3.1-flash-lite"})
        assert r.json()["model"] == "gemini-3.1-flash-lite", r.json()
        tidm = r.json()["id"]
        r = await c.patch(f"/api/tasks/{tidm}", json={"model": "gemini-3.5-flash"})
        assert r.json()["model"] == "gemini-3.5-flash"
        r = await c.patch(f"/api/tasks/{tidm}", json={"model": "gpt-4"})
        assert r.status_code == 422, r.status_code
        r = await c.post("/api/tasks", json={"title": "bad", "model": "gpt-4"})
        assert r.status_code == 422
        # default when unspecified
        r = await c.post("/api/tasks", json={"title": "dflt"})
        assert r.json()["model"] == "gemini-3.6-flash", r.json()
        print("ok    model: listed, set at create, patched, invalid rejected (422), default applied")

        # 6b. run narrated progress into the thread
        r = await c.get(f"/api/tasks/{tid}")
        notes = [m["text"] for m in r.json()["messages"] if m["role"] == "agent"]
        assert any("Sandbox is up" in n for n in notes), notes
        assert any("checks green" in n for n in notes), notes
        print("ok    run posted progress + completion messages")

        # 6f. SSE stream: verified against the LIVE stack (nginx + uvicorn),
        # not the ASGI transport. httpx's ASGITransport cannot stream a
        # StreamingResponse (Starlette's disconnect listener blocks on a
        # receive() that never fires in the fake transport), so in-process
        # streaming is not representative. The browser path is what matters;
        # we exercise it here over real HTTP.
        import httpx as _hx
        live = os.environ.get("AGS_LIVE_URL", "").rstrip("/")
        # The live api signs sessions with its own SESSION_SECRET (from
        # .env.local), which the test's load() does not see. AGS_LIVE_COOKIE
        # carries a cookie minted inside the live container for the stream.
        live_cookie = os.environ.get("AGS_LIVE_COOKIE", "")
        if live and live_cookie:
            async with _hx.AsyncClient(base_url=live, timeout=None,
                                       headers={"cookie": f"ags_session={live_cookie}"}) as lc:
                rr = await lc.post("/api/tasks", json={"title": "sse-live"})
                lid = rr.json()["id"]
                seen, status = [], {}
                async def _listen():
                    async with lc.stream("GET", f"/api/tasks/{lid}/events") as resp:
                        status["code"] = resp.status_code
                        status["ct"] = resp.headers.get("content-type", "")
                        async for line in resp.aiter_lines():
                            if line.startswith("data:"):
                                seen.append(line)
                            if len(seen) >= 1:  # snapshot proves the stream flows
                                break
                task = asyncio.create_task(_listen())
                await asyncio.wait_for(task, timeout=8)
                assert status["code"] == 200, status
                assert status["ct"].startswith("text/event-stream"), status
                assert seen and '"changed": true' in seen[0], seen
                print(f"ok    SSE stream (live): snapshot event received ({status['ct']})")
        else:
            print("skip  SSE live stream (set AGS_LIVE_URL + AGS_LIVE_COOKIE to run)")

        # 7. other user cannot see the task
        c.cookies.set("ags_session", _signed_cookie(settings, sub="user-2"))
        r = await c.get(f"/api/tasks/{tid}")
        assert r.status_code == 404, r.status_code
        r = await c.get(f"/api/tasks/{tid}/events")
        assert r.status_code == 404, r.status_code  # no cross-owner stream
        r = await c.get("/api/tasks")
        assert r.json()["tasks"] == []
        print("ok    tasks are scoped to their owner")

    print("\nTasks flow: all checks passed")


asyncio.run(main())
