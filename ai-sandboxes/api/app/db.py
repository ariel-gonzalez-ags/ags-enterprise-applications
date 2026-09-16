"""Async SQLAlchemy engine + session factory. SQLite file lives on the
compose volume (/data) so state survives container rebuilds."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init(db_path: str) -> None:
    global _engine, _session_factory
    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def create_schema() -> None:
    assert _engine is not None, "db.init() must run first"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def session() -> AsyncSession:
    assert _session_factory is not None, "db.init() must run first"
    return _session_factory()
