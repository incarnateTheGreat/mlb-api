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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db, close_db
from app.middleware import CSRFMiddleware
from app.routers import (
    auth,
    games,
    players,
    matchups,
    analysis,
    notifications,
    standings,
    teams,
)
from app.services import push_service
from app.services.scoring_watcher import run_scoring_watcher

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager for startup/shutdown events.
    
    This is FastAPI's way of handling app lifecycle —
    similar to Next.js instrumentation or Remix's entry.server.tsx.
    Code before `yield` runs on startup, code after runs on shutdown.
    """
    # Startup
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

# CORS configuration — allow your React Router frontend
# In production, restrict origins to your actual domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Production server
        "http://localhost:5173",  # Vite dev server (default)
        "http://localhost:5174",  # Vite dev server (alternate)
        get_settings().frontend_url,  # Configured frontend URL
    ],
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
app.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
app.include_router(standings.router, prefix="/standings", tags=["standings"])
app.include_router(teams.router, prefix="/teams", tags=["teams"])
app.include_router(notifications.router)  # Routes are /notifications/*


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers and monitoring."""
    return {"status": "healthy", "debug": get_settings().debug}


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "MLB API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
