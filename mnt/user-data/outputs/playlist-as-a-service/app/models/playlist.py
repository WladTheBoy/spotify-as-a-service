"""
app/models/playlist.py
──────────────────────
ORM models map Python classes → database tables.

Design choices:
  • Separate Playlist and Track tables (normalised schema).
  • playlist_db_id is our internal UUID; spotify_playlist_id is Spotify's.
  • Tracks store enough data to answer all endpoints without re-hitting Spotify.
  • JSON columns (artist_genres) store enriched data that's expensive to fetch.
"""

import json
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Playlist(Base):
    __tablename__ = "playlists"

    # ── Primary key ────────────────────────────────────────────────────────
    id: Mapped[str] = mapped_column(String(22), primary_key=True)
    # Short human-friendly ID generated via shortuuid (e.g. "abc123XY")

    # ── Spotify metadata ───────────────────────────────────────────────────
    spotify_playlist_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    spotify_url: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[str] = mapped_column(String(128))
    cover_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    total_tracks: Mapped[int] = mapped_column(Integer, default=0)

    # ── Housekeeping ───────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # ── Relationships ──────────────────────────────────────────────────────
    tracks: Mapped[list["Track"]] = relationship(
        "Track", back_populates="playlist", cascade="all, delete-orphan"
    )

    @property
    def api_url(self) -> str:
        return f"/api/playlists/{self.id}"

    def __repr__(self) -> str:
        return f"<Playlist id={self.id} name={self.name!r}>"


class Track(Base):
    __tablename__ = "tracks"

    # ── Primary key ────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Foreign key ────────────────────────────────────────────────────────
    playlist_id: Mapped[str] = mapped_column(
        String(22), ForeignKey("playlists.id", ondelete="CASCADE"), index=True
    )

    # ── Spotify track metadata ─────────────────────────────────────────────
    spotify_track_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(512))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)
    popularity: Mapped[int] = mapped_column(Integer, default=0)
    preview_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    external_url: Mapped[str] = mapped_column(String(512), default="")
    album_name: Mapped[str] = mapped_column(String(512), default="")
    album_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    album_release_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    track_number: Mapped[int] = mapped_column(Integer, default=0)

    # ── Artists stored as JSON (one track → multiple artists) ──────────────
    # e.g. '[{"id": "abc", "name": "Artist X"}]'
    _artists_json: Mapped[str] = mapped_column("artists_json", Text, default="[]")
    _genres_json: Mapped[str] = mapped_column("genres_json", Text, default="[]")

    # ── Position in the playlist ───────────────────────────────────────────
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────
    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="tracks")

    # ── JSON helpers ───────────────────────────────────────────────────────
    @property
    def artists(self) -> list[dict]:
        return json.loads(self._artists_json)

    @artists.setter
    def artists(self, value: list[dict]) -> None:
        self._artists_json = json.dumps(value)

    @property
    def genres(self) -> list[str]:
        return json.loads(self._genres_json)

    @genres.setter
    def genres(self, value: list[str]) -> None:
        self._genres_json = json.dumps(value)

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000

    def __repr__(self) -> str:
        return f"<Track id={self.id} name={self.name!r}>"
