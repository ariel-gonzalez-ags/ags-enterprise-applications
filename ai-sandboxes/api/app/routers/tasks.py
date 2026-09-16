"""Product API: tasks, brainstorm messages, plan approval. All routes are
gated by the signed session cookie and scoped to the owning user."""
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
        await s.refresh(t, ["messages", "artifacts"])
        return _task_json(t, detail=True)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/tasks/{task_id}/messages", status_code=201)
async def chat(task_id: str, body: ChatIn, request: Request, user: dict = Depends(_user)):
    settings = _settings(request)
    async with db.session() as s:
        t = await s.get(Task, task_id)
        if t is None or t.owner_sub != user["sub"]:
            raise HTTPException(404, "task not found")
        if t.state not in ("drafting", "planned"):
            raise HTTPException(409, f"task is {t.state}; chat is closed")
        s.add(Message(task_id=t.id, role="user", text=body.text.strip()))
        await s.commit()
        await s.refresh(t, ["messages"])
        history = [{"role": m.role, "text": m.text} for m in t.messages]

    try:
        out = await planner.reply(settings, history)
    except planner.PlannerUnavailable:
        raise HTTPException(503, "planner not configured (GEMINI_API_KEY)")

    async with db.session() as s:
        t = await s.get(Task, task_id)
        agent_msg = Message(task_id=t.id, role="agent", text=out["reply"], plan_json=out["plan"])
        s.add(agent_msg)
        if out["title"] and t.title in ("", "Untitled task"):
            t.title = out["title"][:200]
        if out["plan"]:
            t.state = "planned"
            t.provider = (out["plan"].get("clouds") or [t.provider])[0]
            t.formats = [d.get("id") for d in out["plan"].get("deliverables", [])
                         if isinstance(d.get("id"), str)] or t.formats
            est = out["plan"].get("est_hours")
            if isinstance(est, (int, float)) and 0 < est <= 24:
                t.max_hours = int(est)
        await s.commit()
        await s.refresh(t, ["messages", "artifacts"])
        return {"message": {"role": "agent", "text": agent_msg.text,
                            "plan": agent_msg.plan_json, "at": agent_msg.created_at},
                "task": _task_json(t, detail=True)}


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
