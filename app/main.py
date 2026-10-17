"""
FastAPI application entry point.

FastAPI is similar to Express but with built-in OpenAPI docs,
request validation via Pydantic, and native async support.
Run with: uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager, suppress
from typing import AsyncGenerator

import asyncio
import logging
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings, is_production
from app.database import init_db, close_db
from app.middleware import CSRFMiddleware
from app.routers import (
    auth,
    games,
    players,
    matchups,
    analysis,
    notifications,
    preview,
    search,
    standings,
    teams,
)
from app.services import db_metrics, push_service
from app.services.scoring_watcher import run_scoring_watcher

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Make the app's own log records visible.

    uvicorn configures only the "uvicorn*" loggers and leaves the root logger
    alone, so records from `app.*` fall through to `logging.lastResort`, which
    drops anything below WARNING. Every logger.info() in the app - including
    the scoring watcher's record of which pushes were delivered - was being
    discarded in production, which is what made a missed notification
    impossible to diagnose after the fact.

    Deliberately scoped to the `app` namespace rather than the root logger:
    SQLAlchemy attaches its own handler when `echo` is on, so configuring the
    root would print every statement twice.

    Records are split across stdout and stderr by level because Railway
    derives a log's severity from the stream it arrived on. Sending INFO to
    stderr tagged routine delivery records as "error", which is exactly what
    makes the undelivered-play ERROR impossible to pick out of the noise.
    """
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    app_logger = logging.getLogger("app")

    # Re-entering the lifespan must not stack handlers and duplicate output.
    if not app_logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )

        routine = logging.StreamHandler(sys.stdout)
        routine.setFormatter(formatter)
        routine.addFilter(lambda record: record.levelno < logging.WARNING)

        problems = logging.StreamHandler(sys.stderr)
        problems.setFormatter(formatter)
        problems.setLevel(logging.WARNING)

        app_logger.addHandler(routine)
        app_logger.addHandler(problems)

    app_logger.setLevel(level)

    # The handlers above are the ones that emit; propagating as well would
    # duplicate records through any root handler.
    app_logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager for startup/shutdown events.
    
    This is FastAPI's way of handling app lifecycle —
    similar to Next.js instrumentation or Remix's entry.server.tsx.
    Code before `yield` runs on startup, code after runs on shutdown.
    """
    # Startup
    _configure_logging()

    await init_db()

    # The scoring play watcher polls MLB on the server's own schedule so push
    # notifications can fire while every subscriber's browser is closed.
    watcher_task: asyncio.Task | None = None
    settings = get_settings()

    if settings.watcher_enabled and push_service.is_push_configured():
        watcher_task = asyncio.create_task(run_scoring_watcher())
    elif settings.watcher_enabled:
        logger.warning(
            "Scoring watcher disabled: VAPID keys are not configured."
        )

    yield

    # Shutdown
    if watcher_task is not None:
        watcher_task.cancel()

        # The watcher re-raises CancelledError to stop; that's expected here.
        with suppress(asyncio.CancelledError):
            await watcher_task

    await close_db()


app = FastAPI(
    title="MLB API",
    description="FastAPI backend for MLB stats and AI-powered analysis",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration — allow your React Router frontend.
# Credentials are sent cross-origin, so the origin list must stay an explicit
# allowlist: "*" is invalid with allow_credentials=True and would be a hole.
# The localhost entries exist for local development only and are dropped in
# production, where a malicious app bound to a dev port could otherwise call
# this API with the user's cookies attached.
_cors_origins = [get_settings().frontend_url]
if not is_production():
    _cors_origins += [
        "http://localhost:3000",
        "http://localhost:5173",  # Vite dev server (default)
        "http://localhost:5174",  # Vite dev server (alternate)
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(_cors_origins)),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# CSRF protection middleware
# Validates X-CSRF-Token header matches csrf_token cookie on mutative requests
app.add_middleware(CSRFMiddleware)

# Register routers (like Express Router or Remix route modules)
app.include_router(auth.router)  # No prefix, routes are /auth/*
app.include_router(games.router, prefix="/games", tags=["games"])
app.include_router(players.router, prefix="/players", tags=["players"])
app.include_router(matchups.router, prefix="/matchups", tags=["matchups"])
app.include_router(preview.router, prefix="/games", tags=["preview"])
app.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
app.include_router(standings.router, prefix="/standings", tags=["standings"])
app.include_router(teams.router, prefix="/teams", tags=["teams"])
app.include_router(search.router, prefix="/search", tags=["search"])
app.include_router(notifications.router)  # Routes are /notifications/*


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers and monitoring."""
    # Database usage rides along because there is nowhere else to read it:
    # Neon's consumption API is paid-plan only, and the Console keeps a single
    # day of history on Free. Aggregate counters only - no query text, no row
    # data, nothing tied to a user.
    usage = db_metrics.snapshot()

    return {
        "status": "healthy",
        "debug": get_settings().debug,
        "database": {
            "queries": usage.queries,
            "connections": usage.connections,
            "awakeHours": round(usage.awake_hours, 3),
            "uptimeHours": round(usage.uptime_seconds / 3600, 3),
            "awakePercent": round(usage.awake_fraction * 100, 1),
            "estimatedCuHours": round(usage.cu_hours, 3),
        },
    }


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "MLB API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
