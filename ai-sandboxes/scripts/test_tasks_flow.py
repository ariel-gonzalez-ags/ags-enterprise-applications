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

import httpx
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

        # 3. chat without planner key → 503
        with mock.patch("app.routers.tasks.planner.reply",
                        side_effect=__import__("app.planner", fromlist=["PlannerUnavailable"]).PlannerUnavailable):
            r = await c.post(f"/api/tasks/{tid}/messages", json={"text": "hi"})
            assert r.status_code == 503, r.status_code
        print("ok    chat without planner returns 503")

        # 4. chat with mocked planner → agent reply + plan → planned
        with mock.patch("app.routers.tasks.planner.reply",
                        new=mock.AsyncMock(return_value=MOCK_PLAN)):
            r = await c.post(f"/api/tasks/{tid}/messages",
                             json={"text": "Harden my Ubuntu VMs to CIS L2"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["message"]["plan"]["deliverables"][0]["id"] == "ansible"
        t = body["task"]
        assert t["state"] == "planned", t["state"]
        assert t["title"] == "Harden Ubuntu to CIS L2"
        assert t["provider"] == "azure" and "ansible" in t["formats"]
        assert t["config"]["maxHours"] == 3
        print("ok    mock plan flips task to planned with config")

        # 5. approve → running, then simulated run lands at verified.
        # Spawn is mocked out so the test drives the run deterministically.
        with mock.patch("app.routers.tasks.runner.spawn"):
            r = await c.post(f"/api/tasks/{tid}/approve")
        assert r.status_code == 200 and r.json()["state"] == "running"
        print("ok    approve flips to running")

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

        # 6. chat closed after planning+run
        r = await c.post(f"/api/tasks/{tid}/messages", json={"text": "more?"})
        assert r.status_code == 409, r.status_code
        print("ok    chat rejected once task is verified")

        # 7. other user cannot see the task
        c.cookies.set("ags_session", _signed_cookie(settings, sub="user-2"))
        r = await c.get(f"/api/tasks/{tid}")
        assert r.status_code == 404, r.status_code
        r = await c.get("/api/tasks")
        assert r.json()["tasks"] == []
        print("ok    tasks are scoped to their owner")

    print("\nTasks flow: all checks passed")


asyncio.run(main())
