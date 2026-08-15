"""Database engine, session handling, and the declarative base every table hangs off.

Async SQLAlchemy 2.0 over asyncpg. Every module that needs a table imports `Base` from
here so that `create_all()` sees the whole schema:

    from app.core.db import Base, JSONB, session_scope, utcnow

    class MyTable(Base):
        __tablename__ = "my_table"
        ...

Two standing rules that this module cannot enforce for you:

* HR-1 — the `source_store` table is **append-only** and only `app/providers/*` may
  write to it. Everyone else holds `source_id`s and reads.
* HR-3 — do not wrap a session in a bare `except` that swallows the failure. A write
  that did not happen must surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

__all__ = [
    "Base",
    "JSONB",
    "get_engine",
    "get_sessionmaker",
    "get_session",
    "session_scope",
    "create_all",
    "dispose_engine",
    "utcnow",
]

# Predictable constraint names, so migrations and `DROP CONSTRAINT` are writable by hand.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """Timezone-aware UTC now. Use this, never `datetime.utcnow()`."""
    return datetime.now(UTC)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session per request, rolled back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope for background work: commits on success, rolls back on error.

    The rollback re-raises. It is here to leave the database consistent, not to hide
    the failure from the caller (HR-3).
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Create any missing tables.

    Adequate for a single-service project with no production data to migrate. If we
    ever need column changes against live data, that is an Alembic decision and needs
    an ADR — do not start hand-patching schemas here.
    """
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Close pooled connections at shutdown."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
