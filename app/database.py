"""
Neon Postgres database connection and session management.

Uses asyncpg under the hood via SQLAlchemy's async driver.
The pattern here is similar to Prisma's client instantiation in JS —
we create a single engine and session factory, then inject sessions
into route handlers via FastAPI's dependency injection.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


# Create async engine for Neon Postgres
# The connection string should use postgresql+asyncpg:// prefix
def get_database_url() -> str:
    """
    Convert standard postgres:// URL to async-compatible format.
    
    Neon Postgres URLs include parameters like sslmode and channel_binding
    that asyncpg doesn't support as URL params. This function:
    1. Changes the scheme to postgresql+asyncpg://
    2. Strips unsupported URL parameters (SSL is handled via connect_args)
    """
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
    
    url = get_settings().database_url
    
    # Parse the URL
    parsed = urlparse(url)
    
    # Change the scheme to postgresql+asyncpg
    if parsed.scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    else:
        scheme = parsed.scheme
    
    # Filter query parameters - remove ones that asyncpg doesn't support
    # asyncpg handles SSL via connect_args, not URL params
    unsupported_params = {'sslmode', 'ssl', 'channel_binding', 'options'}
    query_params = parse_qs(parsed.query)
    filtered_params = {k: v for k, v in query_params.items() if k not in unsupported_params}
    new_query = urlencode(filtered_params, doseq=True)
    
    # Rebuild the URL
    new_parsed = parsed._replace(scheme=scheme, query=new_query)
    return urlunparse(new_parsed)


# Lazy initialization — engine is created on first access, not at import time
# This allows tests to import the module without needing a valid DATABASE_URL
_engine = None
_async_session_maker = None


def get_engine():
    """Get or create the async database engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_database_url(),
            echo=get_settings().debug,  # Log SQL queries in debug mode
            pool_pre_ping=True,  # Verify connections before using (important for serverless DBs)
            connect_args={"ssl": True},  # Enable SSL for Neon
        )
    return _engine


def get_session_maker():
    """Get or create the async session factory."""
    global _async_session_maker
    if _async_session_maker is None:
        # Session factory — creates new sessions for each request
        # expire_on_commit=False keeps objects accessible after commit (like Prisma's behavior)
        _async_session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_maker


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that yields a database session.
    
    In FastAPI, dependencies are like React context providers —
    they inject values into route handlers. This generator pattern
    ensures the session is properly closed after the request.
    
    Usage in a route:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Initialize database tables.
    Call this on app startup to ensure cache tables exist.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections on shutdown."""
    engine = get_engine()
    await engine.dispose()
