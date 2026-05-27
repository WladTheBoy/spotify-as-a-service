"""
app/schemas/playlist.py
────────────────────────
Pydantic v2 schemas decouple the API contract from internal ORM models.
This means we can change the DB schema without breaking API consumers,
and vice versa.

Naming convention:
  • *Create  — incoming request body
  • *Response — outgoing response (what the client sees)
  • *Detail   — extended response with nested data
"""

from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl, field_validator
import re


# ── Request Schemas ────────────────────────────────────────────────────────

class PlaylistCreate(BaseModel):
    """POST /playlists — submit a Spotify playlist URL."""

    spotify_url: str = Field(
        ...,
        description="Full Spotify playlist URL or URI",
        examples=["https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"],
    )

    @field_validator("spotify_url")
    @classmethod
    def validate_spotify_url(cls, v: str) -> str:
        patterns = [
            r"https://open\.spotify\.com/playlist/([A-Za-z0-9]+)",
            r"spotify:playlist:([A-Za-z0-9]+)",
        ]
        for p in patterns:
            if re.search(p, v):
                return v
        raise ValueError(
            "URL must be a valid Spotify playlist link "
            "(e.g. https://open.spotify.com/playlist/abc123)"
        )


class PlaylistRefreshRequest(BaseModel):
    """POST /playlists/{id}/refresh — force re-sync from Spotify."""
    force: bool = Field(default=False, description="Bypass cache and force full re-sync")


# ── Nested Schemas ─────────────────────────────────────────────────────────

class ArtistSchema(BaseModel):
    id: str
    name: str


class TrackResponse(BaseModel):
    """A single track in a clean, developer-friendly shape."""

    id: str = Field(description="Spotify track ID")
    name: str
    artists: list[ArtistSchema]
    album: str
    album_image_url: str | None
    album_release_date: str | None
    duration_ms: int
    duration_seconds: float
    explicit: bool
    popularity: int
    preview_url: str | None
    external_url: str
    position: int
    added_at: str | None
    genres: list[str]

    model_config = {"from_attributes": True}


# ── Playlist Response Schemas ──────────────────────────────────────────────

class PlaylistSummaryResponse(BaseModel):
    """Returned immediately after POST /playlists — minimal info."""

    id: str
    name: str
    api_url: str
    total_tracks: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PlaylistResponse(BaseModel):
    """Full playlist metadata — GET /api/playlists/{id}."""

    id: str
    spotify_playlist_id: str
    spotify_url: str
    name: str
    description: str | None
    owner_name: str
    cover_image_url: str | None
    follower_count: int
    total_tracks: int
    is_public: bool
    api_url: str
    created_at: datetime
    last_synced_at: datetime

    # Computed links for discoverability
    tracks_url: str = ""
    random_track_url: str = ""
    artists_url: str = ""
    analytics_url: str = ""

    model_config = {"from_attributes": True}

    def model_post_init(self, __context: object) -> None:
        base = f"/api/playlists/{self.id}"
        self.tracks_url = f"{base}/tracks"
        self.random_track_url = f"{base}/random"
        self.artists_url = f"{base}/artists"
        self.analytics_url = f"{base}/analytics"


# ── Track List Response ────────────────────────────────────────────────────

class PaginatedTracksResponse(BaseModel):
    """GET /api/playlists/{id}/tracks — paginated track list."""

    playlist_id: str
    playlist_name: str
    total: int
    page: int
    page_size: int
    total_pages: int
    tracks: list[TrackResponse]


# ── Artist Schemas ─────────────────────────────────────────────────────────

class ArtistStatSchema(BaseModel):
    name: str
    track_count: int
    tracks: list[str]  # track names


class TopArtistsResponse(BaseModel):
    """GET /api/playlists/{id}/artists"""

    playlist_id: str
    playlist_name: str
    total_unique_artists: int
    artists: list[ArtistStatSchema]


# ── Analytics Schemas ──────────────────────────────────────────────────────

class GenreStatSchema(BaseModel):
    genre: str
    count: int
    percentage: float


class PlaylistAnalyticsResponse(BaseModel):
    """GET /api/playlists/{id}/analytics"""

    playlist_id: str
    playlist_name: str
    total_tracks: int
    total_duration_ms: int
    total_duration_minutes: float
    average_popularity: float
    explicit_tracks: int
    explicit_percentage: float
    unique_artists: int
    unique_albums: int
    top_genres: list[GenreStatSchema]
    popularity_distribution: dict[str, int]  # "0-20", "21-40" …
    tracks_with_preview: int


# ── Search ─────────────────────────────────────────────────────────────────

class SearchResponse(BaseModel):
    playlist_id: str
    query: str
    total_results: int
    tracks: list[TrackResponse]


# ── Export ─────────────────────────────────────────────────────────────────

class ExportResponse(BaseModel):
    playlist_id: str
    exported_at: datetime
    format: str = "json"
    playlist: PlaylistResponse
    tracks: list[TrackResponse]
