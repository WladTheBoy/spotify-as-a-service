"""
app/main.py
───────────
FastAPI application factory.

Lifespan context manager (preferred over @app.on_event) handles:
  • DB table creation on startup
  • Redis connection (if enabled)
  • Graceful shutdown of HTTP clients

Custom OpenAPI metadata gives the Swagger UI a branded, useful appearance.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from slowapi.errors import RateLimitExceeded

from app.core.cache import cache
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.rate_limit import limiter
from app.database import init_db
from app.routes import api, health, playlists
from app.services.spotify import spotify_service

settings = get_settings()
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Runs setup before the app starts serving, teardown after it stops.
    This replaces the deprecated @app.on_event("startup") pattern.
    """
    logger.info("🚀 Starting Playlist-as-a-Service")

    # 1. Create DB tables
    await init_db()
    logger.info("Database tables ready")

    # 2. Connect to Redis if enabled
    if settings.use_redis:
        await cache.connect()  # type: ignore[attr-defined]

    yield  # ← application runs here

    # Teardown
    logger.info("Shutting down…")
    await spotify_service.close()
    if settings.use_redis:
        await cache.close()  # type: ignore[attr-defined]
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="🎵 Playlist-as-a-Service",
        description=(
            "## Turn any public Spotify playlist into a REST API\n\n"
            "### Quick start\n"
            "1. `POST /playlists` with a Spotify URL\n"
            "2. Use the returned `api_url` to query your playlist\n\n"
            "### Features\n"
            "- Paginated track listing with filtering\n"
            "- Random track endpoint\n"
            "- Top artists & genre analytics\n"
            "- Full-text search\n"
            "- JSON export\n"
            "- Automatic caching\n"
        ),
        version="0.1.0",
        contact={
            "name": "Playlist-as-a-Service",
            "url": "https://github.com/your-org/playlist-as-a-service",
        },
        license_info={"name": "MIT"},
        lifespan=lifespan,
        # Serve docs at standard paths
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # ── Rate limiting ──────────────────────────────────────────────────────
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": f"Rate limit exceeded: {exc.detail}"},
        )

    # ── CORS ───────────────────────────────────────────────────────────────
    # Permissive in dev; tighten origins in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ────────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(playlists.router)
    app.include_router(api.router)

    return app


app = create_app()
