"""
Async SQLAlchemy engine + session factory.

DATABASE_URL defaults to SQLite (for local dev without Postgres).
Set DATABASE_URL=postgresql+asyncpg://... in .env for production.
"""
from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base

_DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./conversion_engine.db",
)

# Replace postgres:// or postgresql:// with postgresql+asyncpg:// for async Python compatibility
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif _DATABASE_URL.startswith("postgresql://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    _DATABASE_URL,
    echo=False,
    future=True,
    # SQLite-specific: enable WAL mode via connect_args
    connect_args={"check_same_thread": False} if "sqlite" in _DATABASE_URL else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all tables (idempotent — safe to call on every startup)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add columns that were introduced after initial schema creation.
        # These are no-ops if the column already exists (Postgres only).
        if "postgresql" in str(conn.engine.url):
            for stmt in [
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS resend_email_id VARCHAR(128) UNIQUE",
            ]:
                await conn.execute(__import__("sqlalchemy").text(stmt))


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async session."""
    async with AsyncSessionLocal() as session:
        yield session
