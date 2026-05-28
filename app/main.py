"""
FastAPI application entry point.

FastAPI is similar to Express but with built-in OpenAPI docs,
request validation via Pydantic, and native async support.
Run with: uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db, close_db
from app.routers import games, players, matchups, analysis, standings


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
    yield
    # Shutdown
    await close_db()


app = FastAPI(
    title="MLB API",
    description="FastAPI backend for MLB stats and AI-powered analysis",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration — allow your Remix frontend
# In production, restrict origins to your actual domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Remix dev server
        "http://localhost:5173",  # Vite dev server
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers (like Express Router or Remix route modules)
app.include_router(games.router, prefix="/games", tags=["games"])
app.include_router(players.router, prefix="/players", tags=["players"])
app.include_router(matchups.router, prefix="/matchups", tags=["matchups"])
app.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
app.include_router(standings.router, prefix="/standings", tags=["standings"])


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
