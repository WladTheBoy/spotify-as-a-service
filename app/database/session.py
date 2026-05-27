"""
app/database/session.py
───────────────────────
Async SQLAlchemy engine + session factory.

Why async?  FastAPI is async-first. Using sync DB calls inside async
route handlers would block the event loop. aiosqlite / asyncpg keep
everything non-blocking.

Swap DATABASE_URL to postgresql+asyncpg://... for production.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings

settings = get_settings()

# connect_args is SQLite-specific — prevents "object created in thread" errors
connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}

engine = create_async_engine(
    settings.database_url,
    echo=not settings.is_production,   # SQL logging in dev
    connect_args=connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep objects accessible after commit
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """All ORM models inherit from this base — enables create_all()."""
    pass


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """
    FastAPI dependency that yields a DB session per request.
    The session is always closed — even if the route raises an exception.
    """
    async with AsyncSessionLocal() as session:
        yield session
