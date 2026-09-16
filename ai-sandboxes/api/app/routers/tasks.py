"""Product API: tasks, brainstorm messages, plan approval. All routes are
gated by the signed session cookie and scoped to the owning user."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import db, planner, runner
from ..config import Settings
from ..models import Message, Task
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
        "formats": t.formats,
        "idempotent": t.idempotent,
        "checks": {"passed": t.checks_passed, "total": t.checks_total},
        "agent_pending": t.agent_pending,
        "updated": t.updated_at,
    }
    if detail:
        out["config"] = {
            "destroyAfter": t.destroy_after,
            "maxHours": t.max_hours,
        }
        out["messages"] = [
            {"role": m.role, "text": m.text, "plan": m.plan_json, "at": m.created_at}
            for m in t.messages
        ]
        out["artifacts"] = [
            {"id": a.filename, "kind": a.kind, "size": a.size, "note": a.note}
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


class CreateTask(BaseModel):
    title: str = Field(default="Untitled task", max_length=200)


@router.post("/tasks", status_code=201)
async def create_task(body: CreateTask, user: dict = Depends(_user)):
    async with db.session() as s:
        t = Task(owner_sub=user["sub"], title=body.title.strip() or "Untitled task")
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


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, user: dict = Depends(_user)):
    """Drafting/planned tasks are disposable; running/verified/delivered ones
    are a record and can't be removed."""
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state not in ("drafting", "planned"):
            raise HTTPException(409, f"task is {t.state}; only drafts can be deleted")
        await s.delete(t)
        await s.commit()


class PatchTask(BaseModel):
    formats: list[str] = Field(min_length=1, max_length=12)


def _slug(raw: str) -> str:
    """Lowercase, spaces to dashes, keep [a-z0-9-_]; matches the client."""
    import re
    return re.sub(r"[^a-z0-9-_]", "", raw.strip().lower().replace(" ", "-"))[:32]

@router.patch("/tasks/{task_id}")
async def patch_task(task_id: str, body: PatchTask, user: dict = Depends(_user)):
    """User edits the accepted deliverable set (toggling plan-card options or
    adding a custom one). Only while the task is still shapeable."""
    clean: list[str] = []
    for f in body.formats:
        slug = _slug(f)
        if slug and slug not in clean:
            clean.append(slug)
    if not clean:
        raise HTTPException(422, "at least one deliverable is required")
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state not in ("drafting", "planned"):
            raise HTTPException(409, f"task is {t.state}; deliverables are locked")
        t.formats = clean
        await s.commit()
        return _task_json(t, detail=True)


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

    try:
        out = await planner.reply(settings, history)
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

    asyncio.get_running_loop().create_task(_plan_in_background(task_id, settings))
    return {"accepted": True, "task_id": task_id}


@router.post("/tasks/{task_id}/approve")
async def approve(task_id: str, user: dict = Depends(_user)):
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state != "planned":
            raise HTTPException(409, f"cannot approve a task in state {t.state}")
        t.state = "running"
        t.checks_passed = 0
        await s.commit()
    runner.spawn(task_id)
    return {"id": task_id, "state": "running"}
