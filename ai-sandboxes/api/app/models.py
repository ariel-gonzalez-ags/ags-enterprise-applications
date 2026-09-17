"""SQLAlchemy models: tasks, chat messages, artifacts.

SQLite for now (single-file, in the api container's volume). The dialect is
abstracted by SQLAlchemy, so Postgres later is a connection-string change.
See AGENTS.md "Evolution path".
"""
import time
import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_task_id() -> str:
    return str(uuid.uuid4())


def now() -> int:
    return int(time.time())


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_task_id)
    owner_sub: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Untitled task")
    state: Mapped[str] = mapped_column(String(16), default="drafting", index=True)
    provider: Mapped[str] = mapped_column(String(16), default="azure")
    model: Mapped[str] = mapped_column(String(40), default="gemini-3.6-flash")
    formats: Mapped[list] = mapped_column(JSON, default=list)
    idempotent: Mapped[bool] = mapped_column(Boolean, default=True)
    destroy_after: Mapped[bool] = mapped_column(Boolean, default=True)
    max_hours: Mapped[int] = mapped_column(Integer, default=4)
    checks_passed: Mapped[int] = mapped_column(Integer, default=0)
    checks_total: Mapped[int] = mapped_column(Integer, default=0)
    agent_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    # Lifecycle stage during a real run (provisioning/agent/verifying/teardown).
    # Drives the console's stage indicator; empty when not running.
    run_stage: Mapped[str] = mapped_column(String(16), default="")
    # Live agent transcript during a run: appended as the executor emits lines
    # so the console can render a live activity feed, not a frozen thread.
    # Persisted; becomes part of the run's evidence after completion.
    run_log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    updated_at: Mapped[int] = mapped_column(Integer, default=now, onupdate=now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Message.created_at",
        lazy="selectin",
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(8))  # user | agent
    text: Mapped[str] = mapped_column(Text)
    plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)

    task: Mapped[Task] = relationship(back_populates="messages")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    size: Mapped[str] = mapped_column(String(16), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[int] = mapped_column(Integer, default=now)

    task: Mapped[Task] = relationship(back_populates="artifacts")


class CostEvent(Base):
    """One row per sandbox run: the chargeback ledger. The org is the
    chargeback unit, owner_sub attributes it to a person, and the resource
    group + tags tie it to real Azure spend (reconciled against Cost
    Management). estimated_usd stays 0 until that reconciliation exists; the
    duration is exact because we create and destroy the sandbox ourselves."""
    __tablename__ = "cost_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(32), default="")
    org_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    owner_sub: Mapped[str] = mapped_column(String(64), index=True, default="")
    provider: Mapped[str] = mapped_column(String(16), default="azure")
    resource_group: Mapped[str] = mapped_column(String(90), default="")
    estimated_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    destroyed_at: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
