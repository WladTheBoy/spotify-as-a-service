"""
app/routes/playlists.py
────────────────────────
Playlist management routes:
  POST /playlists          — import a Spotify playlist
  GET  /playlists          — list all imported playlists
  POST /playlists/{id}/refresh — re-sync from Spotify

Architecture note: routes are intentionally thin.
They validate input (via Pydantic), call a service, return a response.
No business logic lives here.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.rate_limit import limiter
from app.database.session import get_db
from app.schemas.playlist import (
    PlaylistCreate,
    PlaylistRefreshRequest,
    PlaylistSummaryResponse,
)
from app.services.playlist import playlist_service
from app.services.spotify import SpotifyError

router = APIRouter(prefix="/playlists", tags=["Playlist Management"])


@router.post(
    "",
    response_model=PlaylistSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a Spotify playlist",
    description=(
        "Submit a public Spotify playlist URL. The service fetches all metadata "
        "and tracks, stores them locally, and returns a stable API URL you can "
        "use to query the playlist without hitting Spotify again."
    ),
)
@limiter.limit("30/minute")
async def create_playlist(
    request: Request,
    body: PlaylistCreate,
    db: AsyncSession = Depends(get_db),
) -> PlaylistSummaryResponse:
    try:
        return await playlist_service.create_playlist(body.spotify_url, db)
    except SpotifyError as e:
        logger.warning(f"SpotifyError creating playlist: {e}")
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.exception(f"Unexpected error creating playlist: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "",
    summary="List all imported playlists",
    description="Returns a paginated list of all playlists imported into the service.",
)
@limiter.limit("60/minute")
async def list_playlists(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
) -> dict:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    return await playlist_service.list_playlists(db, page=page, page_size=page_size)


@router.post(
    "/{playlist_id}/refresh",
    response_model=PlaylistSummaryResponse,
    summary="Re-sync playlist from Spotify",
    description="Force a full refresh of playlist metadata and tracks from Spotify.",
)
@limiter.limit("10/minute")
async def refresh_playlist(
    request: Request,
    playlist_id: str,
    body: PlaylistRefreshRequest = PlaylistRefreshRequest(),
    db: AsyncSession = Depends(get_db),
) -> PlaylistSummaryResponse:
    try:
        return await playlist_service.refresh_playlist(playlist_id, db)
    except SpotifyError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
