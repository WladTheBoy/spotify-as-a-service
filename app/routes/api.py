"""
app/routes/api.py
──────────────────
Public API endpoints — the "product" this service generates.
Every playlist gets these stable, shareable endpoints:

  GET /api/playlists/{id}            — metadata
  GET /api/playlists/{id}/tracks     — paginated tracks (with filtering)
  GET /api/playlists/{id}/random     — random track
  GET /api/playlists/{id}/artists    — top artists
  GET /api/playlists/{id}/analytics  — statistics
  GET /api/playlists/{id}/search     — full-text search
  GET /api/playlists/{id}/export     — full JSON export
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.rate_limit import limiter
from app.database.session import get_db
from app.schemas.playlist import (
    ExportResponse,
    PaginatedTracksResponse,
    PlaylistAnalyticsResponse,
    PlaylistResponse,
    SearchResponse,
    TopArtistsResponse,
    TrackResponse,
)
from app.services.playlist import playlist_service
from app.services.spotify import SpotifyError

router = APIRouter(prefix="/api/playlists", tags=["Playlist API"])


def _handle_spotify_error(e: SpotifyError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=str(e))


@router.get(
    "/{playlist_id}",
    response_model=PlaylistResponse,
    summary="Get playlist metadata",
    description="Returns full metadata for the imported playlist including owner, cover art, and discoverable API links.",
)
@limiter.limit("120/minute")
async def get_playlist(
    request: Request,
    playlist_id: str,
    db: AsyncSession = Depends(get_db),
) -> PlaylistResponse:
    try:
        return await playlist_service.get_playlist(playlist_id, db)
    except SpotifyError as e:
        raise _handle_spotify_error(e)


@router.get(
    "/{playlist_id}/tracks",
    response_model=PaginatedTracksResponse,
    summary="Get all tracks",
    description=(
        "Returns a paginated list of tracks. Supports filtering by query string, "
        "explicit content, and minimum popularity."
    ),
)
@limiter.limit("120/minute")
async def get_tracks(
    request: Request,
    playlist_id: str,
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=50, ge=1, le=200, description="Tracks per page"),
    search: str | None = Query(default=None, description="Filter by track/album/artist name"),
    explicit: bool | None = Query(default=None, description="Filter explicit tracks"),
    min_popularity: int | None = Query(default=None, ge=0, le=100, description="Minimum popularity (0-100)"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedTracksResponse:
    try:
        return await playlist_service.get_tracks(
            playlist_id,
            db,
            page=page,
            page_size=page_size,
            search=search,
            explicit_only=explicit,
            min_popularity=min_popularity,
        )
    except SpotifyError as e:
        raise _handle_spotify_error(e)


@router.get(
    "/{playlist_id}/random",
    response_model=TrackResponse,
    summary="Get a random track",
    description="Returns a uniformly random track from the playlist. Great for shuffle-style apps.",
)
@limiter.limit("120/minute")
async def get_random_track(
    request: Request,
    playlist_id: str,
    db: AsyncSession = Depends(get_db),
) -> TrackResponse:
    try:
        return await playlist_service.get_random_track(playlist_id, db)
    except SpotifyError as e:
        raise _handle_spotify_error(e)


@router.get(
    "/{playlist_id}/artists",
    response_model=TopArtistsResponse,
    summary="Get top artists",
    description="Lists artists ranked by number of tracks in this playlist.",
)
@limiter.limit("60/minute")
async def get_top_artists(
    request: Request,
    playlist_id: str,
    limit: int = Query(default=20, ge=1, le=100, description="Max number of artists to return"),
    db: AsyncSession = Depends(get_db),
) -> TopArtistsResponse:
    try:
        return await playlist_service.get_top_artists(playlist_id, db, limit=limit)
    except SpotifyError as e:
        raise _handle_spotify_error(e)


@router.get(
    "/{playlist_id}/analytics",
    response_model=PlaylistAnalyticsResponse,
    summary="Get playlist analytics",
    description=(
        "Aggregated statistics: total duration, average popularity, "
        "explicit ratio, top genres, popularity distribution."
    ),
)
@limiter.limit("30/minute")
async def get_analytics(
    request: Request,
    playlist_id: str,
    db: AsyncSession = Depends(get_db),
) -> PlaylistAnalyticsResponse:
    try:
        return await playlist_service.get_analytics(playlist_id, db)
    except SpotifyError as e:
        raise _handle_spotify_error(e)


@router.get(
    "/{playlist_id}/search",
    response_model=SearchResponse,
    summary="Search inside playlist",
    description="Full-text search across track names, album names, and artist names.",
)
@limiter.limit("60/minute")
async def search_tracks(
    request: Request,
    playlist_id: str,
    q: str = Query(..., min_length=1, description="Search query"),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    try:
        return await playlist_service.search_tracks(playlist_id, q, db)
    except SpotifyError as e:
        raise _handle_spotify_error(e)


@router.get(
    "/{playlist_id}/export",
    response_model=ExportResponse,
    summary="Export playlist as JSON",
    description="Full export of playlist metadata and all tracks in a single JSON payload.",
)
@limiter.limit("10/minute")
async def export_playlist(
    request: Request,
    playlist_id: str,
    db: AsyncSession = Depends(get_db),
) -> ExportResponse:
    try:
        return await playlist_service.export_playlist(playlist_id, db)
    except SpotifyError as e:
        raise _handle_spotify_error(e)
