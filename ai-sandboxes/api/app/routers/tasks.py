"""Product API: tasks, brainstorm messages, plan approval. All routes are
gated by the signed session cookie and scoped to the owning user."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import db, embers, events, planner, runner
from ..config import Settings
from ..models import Artifact, EmberLedger, Message, Task
from ..session import get_session

router = APIRouter()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _user(request: Request) -> dict:
    user = get_session(request, _settings(request))
    if not user:
        raise HTTPException(401, "not authenticated")
    return user


def _task_json(t: Task, detail: bool = False) -> dict:
    out = {
        "id": t.id,
        "title": t.title,
        "state": t.state,
        "provider": t.provider,
        "model": t.model,
        "formats": t.formats,
        "idempotent": t.idempotent,
        "checks": {"passed": t.checks_passed, "total": t.checks_total},
        "run_stage": t.run_stage,
        "agent_pending": t.agent_pending,        "updated": t.updated_at,
    }
    if detail:
        out["config"] = {
            "destroyAfter": t.destroy_after,
            "maxHours": t.max_hours,
        }
        out["run_log"] = t.run_log  # live agent transcript during a run
        out["cost"] = {             # Ember burn for this task (0 until it ran)
            "embers": t.embers_spent,
            "sandboxSeconds": t.sandbox_seconds,
            "llmTokens": t.llm_tokens,
        }
        out["messages"] = [
            {"role": m.role, "text": m.text, "plan": m.plan_json, "at": m.created_at}
            for m in t.messages
        ]
        out["artifacts"] = [
            {"id": a.filename, "kind": a.kind, "size": a.size, "note": a.note,
             "url": f"/api/tasks/{t.id}/artifacts/{a.filename}"}
            for a in t.artifacts
        ]
    return out


async def _owned_task(task_id: str, user: dict) -> Task:
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        await s.refresh(t, ["messages", "artifacts"])
        return t


@router.get("/tasks")
async def list_tasks(user: dict = Depends(_user)):
    async with db.session() as s:
        result = await s.execute(
            select(Task)
            .where(Task.owner_sub == user["sub"])
            .order_by(Task.updated_at.desc())
            .limit(50)
        )
        return {"tasks": [_task_json(t) for t in result.scalars()]}


_PROVIDERS = {"azure", "aws", "gcp"}


class CreateTask(BaseModel):
    title: str = Field(default="Untitled task", max_length=200)
    provider: str = Field(default="azure", max_length=16)
    model: str | None = Field(default=None, max_length=40)


@router.get("/models")
async def list_models(user: dict = Depends(_user)):
    """Planner models the user can pick from. Data-driven; the source of
    truth is planner.MODELS."""
    return {"models": planner.MODELS}


@router.get("/embers")
async def get_embers(request: Request, user: dict = Depends(_user)):
    """The caller's Ember balance and recent ledger. Embers are the cost meter:
    1 Ember = $0.01. Trial is granted on first call; top-up comes with Stripe
    (Phase 2)."""
    settings = _settings(request)
    bal = await embers.balance(user["sub"], settings)
    async with db.session() as s:
        rows = (await s.execute(
            select(EmberLedger).where(EmberLedger.owner_sub == user["sub"])
            .order_by(EmberLedger.created_at.desc()).limit(100))).scalars().all()
    spent = [r for r in rows if r.delta < 0]
    granted = sum(r.delta for r in rows if r.delta > 0)
    # Breakdown by planner/agent model for the usage page (which model the
    # Embers went to). Keyed by model id; empty-string model folds to "unknown".
    by_model: dict = {}
    for r in spent:
        m = by_model.setdefault(r.model or "unknown",
                                {"embers": 0, "runs": 0, "llm_tokens": 0,
                                 "sandbox_seconds": 0})
        m["embers"] += -r.delta
        m["runs"] += 1
        m["llm_tokens"] += r.llm_tokens
        m["sandbox_seconds"] += r.sandbox_seconds
    return {
        "balance": bal,
        "peg_usd": settings.ember_peg_usd,
        "trial_allowance": settings.ember_trial_allowance,
        # Aggregates for the usage page: lifetime granted / spent, and the
        # metered quantities behind them (compute seconds, LLM tokens).
        "total_granted": granted,
        "total_spent": -sum(r.delta for r in spent),
        "total_sandbox_seconds": sum(r.sandbox_seconds for r in spent),
        "total_llm_tokens": sum(r.llm_tokens for r in spent),
        "runs": len(spent),
        "by_model": by_model,
        # Per-run history (newest first), one row per burn/grant.
        "recent": [{"delta": r.delta, "reason": r.reason, "task_id": r.task_id,
                    "model": r.model,
                    "sandbox_seconds": r.sandbox_seconds, "llm_tokens": r.llm_tokens,
                    "at": r.created_at} for r in rows],
    }


@router.post("/tasks", status_code=201)
async def create_task(body: CreateTask, user: dict = Depends(_user)):
    provider = body.provider.strip().lower()
    if provider not in _PROVIDERS:
        raise HTTPException(422, f"provider must be one of {sorted(_PROVIDERS)}")
    model = (body.model or "").strip()
    if model and model not in planner.MODEL_IDS:
        raise HTTPException(422, f"model must be one of {sorted(planner.MODEL_IDS)}")
    async with db.session() as s:
        t = Task(owner_sub=user["sub"], title=body.title.strip() or "Untitled task",
                 provider=provider, model=model or "gemini-3.6-flash")
        s.add(t)
        await s.commit()
        return _task_json(t)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: dict = Depends(_user)):
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        return _task_json(t, detail=True)


@router.get("/tasks/{task_id}/artifacts/{filename}")
async def download_artifact(task_id: str, filename: str, user: dict = Depends(_user)):
    """Artifact contents, owner-scoped. `download=1` forces a file download;
    plain GET returns text the in-app viewer renders."""
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        result = await s.execute(
            select(Artifact).where(Artifact.task_id == task_id,
                                   Artifact.filename == filename))
        a = result.scalar_one_or_none()
        if a is None:
            raise HTTPException(404, "artifact not found")
        headers = {
            "Content-Disposition": f'attachment; filename="{a.filename}"',
            "Cache-Control": "private, no-store",
        }
        return PlainTextResponse(a.content, headers=headers)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, user: dict = Depends(_user)):
    """Any non-running task can be deleted; its messages and artifacts go with
    it (cascade). A running task has a live sandbox and must not vanish
    mid-flight: 409 until it finishes."""
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state == "running":
            raise HTTPException(409, "task is running; wait for it to finish before deleting")
        await s.delete(t)
        await s.commit()


class PatchTask(BaseModel):
    formats: list[str] | None = Field(default=None)
    provider: str | None = Field(default=None, max_length=16)
    model: str | None = Field(default=None, max_length=40)
    idempotent: bool | None = Field(default=None)
    destroy_after: bool | None = Field(default=None)
    max_hours: int | None = Field(default=None, ge=1, le=24)


def _slug(raw: str) -> str:
    """Lowercase, whitespace runs to dashes, strip the rest; matches the client.
    "JSON policy format" -> "json-policy-format"."""
    import re
    s = re.sub(r"\s+", "-", raw.strip().lower())
    s = re.sub(r"[^a-z0-9-_]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-")[:32]

@router.patch("/tasks/{task_id}")
async def patch_task(task_id: str, body: PatchTask, user: dict = Depends(_user)):
    """User edits the deliverable set and/or target cloud (toggling plan-card
    options, adding a custom one, picking a provider). Only while shapeable."""
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state not in ("drafting", "planned"):
            raise HTTPException(409, f"task is {t.state}; deliverables are locked")
        if body.formats is not None:
            clean: list[str] = []
            for f in body.formats:
                slug = _slug(f)
                if slug and slug not in clean:
                    clean.append(slug)
            if not clean:
                raise HTTPException(422, "at least one deliverable is required")
            t.formats = clean
        if body.provider is not None:
            provider = body.provider.strip().lower()
            if provider not in _PROVIDERS:
                raise HTTPException(422, f"provider must be one of {sorted(_PROVIDERS)}")
            t.provider = provider
        if body.model is not None:
            model = body.model.strip()
            if model not in planner.MODEL_IDS:
                raise HTTPException(422, f"model must be one of {sorted(planner.MODEL_IDS)}")
            t.model = model
        if body.idempotent is not None:
            t.idempotent = body.idempotent
        if body.destroy_after is not None:
            t.destroy_after = body.destroy_after
        if body.max_hours is not None:
            t.max_hours = body.max_hours
        await s.commit()
        return _task_json(t, detail=True)


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, request: Request, user: dict = Depends(_user)):
    """Server-sent events: one {"changed": true} nudge per state change. The
    payload carries no data; the client re-fetches GET /tasks/{id} on each
    nudge, so the stream can never drift from the DB. Ownership is checked
    once up front (404 across owners, same as every other route)."""
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")

    async def gen():
        try:
            async for _ in events.subscribe(task_id):
                yield "data: {\"changed\": true}\n\n"
                if await request.is_disconnected():
                    break
        except asyncio.CancelledError:
            # Client disconnected mid-stream; the finally block in
            # events.subscribe() cleans up the subscription.
            pass

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # don't let nginx buffer the stream
    })


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


async def _plan_in_background(task_id: str, settings: Settings) -> None:
    """Runs the planner against the persisted conversation, then records the
    reply and any plan. Never raises: failures land as an in-thread message
    so the user is never left staring at a spinner."""
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None:
            return
        history = [{"role": m.role, "text": m.text} for m in t.messages]
        model = t.model
        state = {
            "provider": t.provider,
            "formats": list(t.formats or []),
            "idempotent": t.idempotent,
            "destroy_after": t.destroy_after,
            "max_hours": t.max_hours,
        }

    try:
        out = await planner.reply(settings, history, state, model)
        reply_text, title, plan = out["reply"], out["title"], out["plan"]
    except planner.PlannerUnavailable:
        reply_text, title, plan = (
            "The planner isn't configured yet (missing API key). An admin can set GEMINI_API_KEY.",
            None, None)
    except Exception:
        reply_text, title, plan = (
            "Something went wrong on my side. Send that again?", None, None)

    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None:
            return
        s.add(Message(task_id=t.id, role="agent", text=reply_text, plan_json=plan))
        if title and t.title in ("", "Untitled task"):
            t.title = title[:200]
        if plan and t.state == "drafting":
            t.state = "planned"
            t.provider = (plan.get("clouds") or [t.provider])[0]
            t.formats = [d.get("id") for d in plan.get("deliverables", [])
                         if isinstance(d.get("id"), str)] or t.formats
            est = plan.get("est_hours")
            if isinstance(est, (int, float)) and 0 < est <= 24:
                t.max_hours = int(est)
        t.agent_pending = False
        await s.commit()
        events.publish(task_id)


@router.post("/tasks/{task_id}/messages", status_code=202)
async def chat(task_id: str, body: ChatIn, request: Request, user: dict = Depends(_user)):
    """Accepts the user's message instantly (202) and plans in the background.
    The client polls GET /tasks/{id}: agent_pending=True means a reply is on
    its way. This is the same shape real long-running agents will use; chat
    latency and run duration are no longer coupled to any HTTP request."""
    settings = _settings(request)
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state not in ("drafting", "planned"):
            raise HTTPException(409, f"task is {t.state}; chat is closed")
        if t.agent_pending:
            raise HTTPException(409, "planner is already replying")
        s.add(Message(task_id=t.id, role="user", text=body.text.strip()))
        t.agent_pending = True
        await s.commit()
        events.publish(task_id)

    asyncio.get_running_loop().create_task(_plan_in_background(task_id, settings))
    return {"accepted": True, "task_id": task_id}


@router.post("/tasks/{task_id}/approve")
async def approve(task_id: str, request: Request, user: dict = Depends(_user)):
    settings = _settings(request)
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state != "planned":
            raise HTTPException(409, f"cannot approve a task in state {t.state}")
        # Ember gate: refuse a run the user cannot pay for. Grants the one-time
        # trial allowance on first approval. Real burn is settled at teardown.
        allowed, bal, est = await embers.can_afford(user["sub"], settings)
        if not allowed:
            raise HTTPException(
                402, f"insufficient Embers: balance {bal}, estimated cost {est}. "
                     "Top up to run more sandboxes.")
        t.state = "running"
        t.checks_passed = 0
        await s.commit()
    runner.spawn(task_id, settings)
    return {"id": task_id, "state": "running"}
