"""Async SQLAlchemy engine + session factory. SQLite file lives on the
compose volume (/data) so state survives container rebuilds."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init(db_path: str) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def create_schema() -> None:
    """create_all plus tiny idempotent column migrations. No Alembic at this
    stage; when the schema gets real, that's the moment to introduce it."""
    assert _engine is not None, "db.init() must run first"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(tasks)"))).all()}
        if "agent_pending" not in cols:
            await conn.execute(
                text("ALTER TABLE tasks ADD COLUMN agent_pending BOOLEAN NOT NULL DEFAULT 0")
            )
        if "model" not in cols:
            await conn.execute(
                text("ALTER TABLE tasks ADD COLUMN model VARCHAR(40) NOT NULL DEFAULT 'gemini-3.6-flash'")
            )


def session() -> AsyncSession:
    assert _session_factory is not None, "db.init() must run first"
    return _session_factory()
