"""
app/services/playlist.py
────────────────────────
Orchestration service: coordinates Spotify → DB → Cache pipeline.

This layer keeps route handlers thin — routes just call service methods
and return the result. Business logic lives here, not in routes.
"""

import random
from collections import Counter, defaultdict
from datetime import datetime, timezone

import shortuuid
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache
from app.models.playlist import Playlist, Track
from app.schemas.playlist import (
    ArtistStatSchema,
    ExportResponse,
    GenreStatSchema,
    PaginatedTracksResponse,
    PlaylistAnalyticsResponse,
    PlaylistResponse,
    PlaylistSummaryResponse,
    SearchResponse,
    TopArtistsResponse,
    TrackResponse,
)
from app.services.spotify import SpotifyError, spotify_service


def _make_track_response(track: Track) -> TrackResponse:
    """Convert ORM Track → TrackResponse schema."""
    from app.schemas.playlist import ArtistSchema
    return TrackResponse(
        id=track.spotify_track_id,
        name=track.name,
        artists=[ArtistSchema(**a) for a in track.artists],
        album=track.album_name,
        album_image_url=track.album_image_url,
        album_release_date=track.album_release_date,
        duration_ms=track.duration_ms,
        duration_seconds=track.duration_seconds,
        explicit=track.explicit,
        popularity=track.popularity,
        preview_url=track.preview_url,
        external_url=track.external_url,
        position=track.position,
        added_at=track.added_at,
        genres=track.genres,
    )


class PlaylistService:

    # ── Create / Sync ──────────────────────────────────────────────────────

    async def create_playlist(self, spotify_url: str, db: AsyncSession) -> PlaylistSummaryResponse:
        """
        Main entry point for POST /playlists.
        1. Extract Spotify ID from URL
        2. Check if we already have it
        3. Fetch from Spotify + persist to DB
        """
        spotify_id = spotify_service.extract_playlist_id(spotify_url)

        # Return existing record if we already imported this playlist
        existing = await db.scalar(
            select(Playlist).where(Playlist.spotify_playlist_id == spotify_id)
        )
        if existing:
            logger.info(f"Playlist {spotify_id} already exists as {existing.id}")
            return PlaylistSummaryResponse(
                id=existing.id,
                name=existing.name,
                api_url=existing.api_url,
                total_tracks=existing.total_tracks,
                created_at=existing.created_at,
            )

        # Fetch from Spotify
        raw_playlist = await spotify_service.get_playlist(spotify_id)
        raw_tracks = await spotify_service.get_playlist_tracks(spotify_id)

        # Build ORM objects
        playlist = self._build_playlist(spotify_url, spotify_id, raw_playlist)
        db.add(playlist)
        await db.flush()  # get playlist.id before inserting tracks

        tracks = self._build_tracks(playlist.id, raw_tracks)
        db.add_all(tracks)
        playlist.total_tracks = len(tracks)

        await db.commit()
        await db.refresh(playlist)

        logger.info(f"Created playlist {playlist.id} with {len(tracks)} tracks")
        return PlaylistSummaryResponse(
            id=playlist.id,
            name=playlist.name,
            api_url=playlist.api_url,
            total_tracks=playlist.total_tracks,
            created_at=playlist.created_at,
        )

    async def refresh_playlist(self, playlist_id: str, db: AsyncSession) -> PlaylistSummaryResponse:
        """Re-sync a playlist from Spotify, replacing all tracks."""
        playlist = await self._get_or_404(playlist_id, db)

        # Clear Spotify cache so we get fresh data
        await cache.clear_pattern(f"spotify:playlist:{playlist.spotify_playlist_id}")
        await cache.clear_pattern(f"spotify:tracks:{playlist.spotify_playlist_id}")

        raw_playlist = await spotify_service.get_playlist(playlist.spotify_playlist_id)
        raw_tracks = await spotify_service.get_playlist_tracks(playlist.spotify_playlist_id)

        # Update playlist metadata
        self._update_playlist_metadata(playlist, raw_playlist)

        # Replace all tracks
        await db.execute(
            Track.__table__.delete().where(Track.playlist_id == playlist_id)
        )
        tracks = self._build_tracks(playlist_id, raw_tracks)
        db.add_all(tracks)
        playlist.total_tracks = len(tracks)
        playlist.last_synced_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(playlist)

        # Invalidate API-level cache
        await cache.clear_pattern(f"api:playlist:{playlist_id}")

        logger.info(f"Refreshed playlist {playlist_id}: {len(tracks)} tracks")
        return PlaylistSummaryResponse(
            id=playlist.id,
            name=playlist.name,
            api_url=playlist.api_url,
            total_tracks=playlist.total_tracks,
            created_at=playlist.created_at,
        )

    # ── Read endpoints ─────────────────────────────────────────────────────

    async def get_playlist(self, playlist_id: str, db: AsyncSession) -> PlaylistResponse:
        playlist = await self._get_or_404(playlist_id, db)
        return PlaylistResponse.model_validate(playlist)

    async def list_playlists(
        self, db: AsyncSession, page: int = 1, page_size: int = 20
    ) -> dict:
        total = await db.scalar(select(func.count()).select_from(Playlist))
        playlists = (
            await db.scalars(
                select(Playlist)
                .order_by(Playlist.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, -(-( total or 0) // page_size)),
            "playlists": [PlaylistResponse.model_validate(p) for p in playlists],
        }

    async def get_tracks(
        self,
        playlist_id: str,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        explicit_only: bool | None = None,
        min_popularity: int | None = None,
    ) -> PaginatedTracksResponse:
        playlist = await self._get_or_404(playlist_id, db)

        query = select(Track).where(Track.playlist_id == playlist_id)

        if search:
            query = query.where(
                Track.name.ilike(f"%{search}%")
                | Track.album_name.ilike(f"%{search}%")
            )
        if explicit_only is not None:
            query = query.where(Track.explicit == explicit_only)
        if min_popularity is not None:
            query = query.where(Track.popularity >= min_popularity)

        total = await db.scalar(
            select(func.count()).select_from(query.subquery())
        )
        tracks_orm = (
            await db.scalars(
                query.order_by(Track.position)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()

        return PaginatedTracksResponse(
            playlist_id=playlist_id,
            playlist_name=playlist.name,
            total=total or 0,
            page=page,
            page_size=page_size,
            total_pages=max(1, -(-( total or 0) // page_size)),
            tracks=[_make_track_response(t) for t in tracks_orm],
        )

    async def get_random_track(self, playlist_id: str, db: AsyncSession) -> TrackResponse:
        await self._get_or_404(playlist_id, db)
        count = await db.scalar(
            select(func.count()).where(Track.playlist_id == playlist_id)
        )
        if not count:
            raise SpotifyError("Playlist has no tracks", 404)
        offset = random.randint(0, count - 1)
        track = await db.scalar(
            select(Track)
            .where(Track.playlist_id == playlist_id)
            .offset(offset)
            .limit(1)
        )
        return _make_track_response(track)  # type: ignore[arg-type]

    async def get_top_artists(
        self, playlist_id: str, db: AsyncSession, limit: int = 20
    ) -> TopArtistsResponse:
        playlist = await self._get_or_404(playlist_id, db)
        tracks = (
            await db.scalars(select(Track).where(Track.playlist_id == playlist_id))
        ).all()

        artist_tracks: dict[str, list[str]] = defaultdict(list)
        for track in tracks:
            for artist in track.artists:
                artist_tracks[artist["name"]].append(track.name)

        sorted_artists = sorted(artist_tracks.items(), key=lambda x: -len(x[1]))[:limit]

        return TopArtistsResponse(
            playlist_id=playlist_id,
            playlist_name=playlist.name,
            total_unique_artists=len(artist_tracks),
            artists=[
                ArtistStatSchema(name=name, track_count=len(tnames), tracks=tnames[:5])
                for name, tnames in sorted_artists
            ],
        )

    async def get_analytics(
        self, playlist_id: str, db: AsyncSession
    ) -> PlaylistAnalyticsResponse:
        playlist = await self._get_or_404(playlist_id, db)
        tracks = (
            await db.scalars(select(Track).where(Track.playlist_id == playlist_id))
        ).all()

        if not tracks:
            raise SpotifyError("Playlist has no tracks", 404)

        total_duration = sum(t.duration_ms for t in tracks)
        total_popularity = sum(t.popularity for t in tracks)
        explicit_count = sum(1 for t in tracks if t.explicit)
        tracks_with_preview = sum(1 for t in tracks if t.preview_url)

        all_artists: set[str] = set()
        all_albums: set[str] = set()
        genre_counter: Counter = Counter()

        for track in tracks:
            for artist in track.artists:
                all_artists.add(artist["name"])
            all_albums.add(track.album_name)
            genre_counter.update(track.genres)

        # Popularity buckets: 0-20, 21-40, 41-60, 61-80, 81-100
        buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
        for track in tracks:
            p = track.popularity
            if p <= 20:
                buckets["0-20"] += 1
            elif p <= 40:
                buckets["21-40"] += 1
            elif p <= 60:
                buckets["41-60"] += 1
            elif p <= 80:
                buckets["61-80"] += 1
            else:
                buckets["81-100"] += 1

        total = len(tracks)
        top_genres = [
            GenreStatSchema(
                genre=genre,
                count=count,
                percentage=round(count / total * 100, 1),
            )
            for genre, count in genre_counter.most_common(10)
        ]

        return PlaylistAnalyticsResponse(
            playlist_id=playlist_id,
            playlist_name=playlist.name,
            total_tracks=total,
            total_duration_ms=total_duration,
            total_duration_minutes=round(total_duration / 60000, 1),
            average_popularity=round(total_popularity / total, 1),
            explicit_tracks=explicit_count,
            explicit_percentage=round(explicit_count / total * 100, 1),
            unique_artists=len(all_artists),
            unique_albums=len(all_albums),
            top_genres=top_genres,
            popularity_distribution=buckets,
            tracks_with_preview=tracks_with_preview,
        )

    async def search_tracks(
        self, playlist_id: str, query: str, db: AsyncSession
    ) -> SearchResponse:
        playlist = await self._get_or_404(playlist_id, db)
        q = query.lower()
        tracks_orm = (
            await db.scalars(select(Track).where(Track.playlist_id == playlist_id))
        ).all()

        results = [
            t for t in tracks_orm
            if q in t.name.lower()
            or q in t.album_name.lower()
            or any(q in a["name"].lower() for a in t.artists)
        ]

        return SearchResponse(
            playlist_id=playlist_id,
            query=query,
            total_results=len(results),
            tracks=[_make_track_response(t) for t in results],
        )

    async def export_playlist(
        self, playlist_id: str, db: AsyncSession
    ) -> ExportResponse:
        from datetime import datetime, timezone
        playlist = await self._get_or_404(playlist_id, db)
        tracks_orm = (
            await db.scalars(
                select(Track)
                .where(Track.playlist_id == playlist_id)
                .order_by(Track.position)
            )
        ).all()

        return ExportResponse(
            playlist_id=playlist_id,
            exported_at=datetime.now(timezone.utc),
            playlist=PlaylistResponse.model_validate(playlist),
            tracks=[_make_track_response(t) for t in tracks_orm],
        )

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _get_or_404(self, playlist_id: str, db: AsyncSession) -> Playlist:
        playlist = await db.scalar(
            select(Playlist).where(Playlist.id == playlist_id)
        )
        if not playlist:
            raise SpotifyError(f"Playlist '{playlist_id}' not found", 404)
        return playlist

    def _build_playlist(
        self, spotify_url: str, spotify_id: str, raw: dict
    ) -> Playlist:
        images = raw.get("images", [])
        cover = images[0]["url"] if images else None
        return Playlist(
            id=shortuuid.uuid()[:8],
            spotify_playlist_id=spotify_id,
            spotify_url=spotify_url,
            name=raw.get("name", ""),
            description=raw.get("description") or None,
            owner_name=raw.get("owner", {}).get("display_name", ""),
            owner_id=raw.get("owner", {}).get("id", ""),
            cover_image_url=cover,
            follower_count=raw.get("followers", {}).get("total", 0),
            is_public=raw.get("public", True),
            total_tracks=raw.get("tracks", {}).get("total", 0),
        )

    def _update_playlist_metadata(self, playlist: Playlist, raw: dict) -> None:
        images = raw.get("images", [])
        playlist.name = raw.get("name", playlist.name)
        playlist.description = raw.get("description") or playlist.description
        playlist.cover_image_url = images[0]["url"] if images else playlist.cover_image_url
        playlist.follower_count = raw.get("followers", {}).get("total", playlist.follower_count)

    def _build_tracks(
        self, playlist_id: str, raw_items: list[dict]
    ) -> list[Track]:
        tracks = []
        for position, item in enumerate(raw_items):
            t = item.get("track", {})
            if not t or not t.get("id"):
                continue

            album = t.get("album", {})
            album_images = album.get("images", [])
            album_image = album_images[0]["url"] if album_images else None

            artists = [{"id": a["id"], "name": a["name"]} for a in t.get("artists", [])]

            track = Track(
                playlist_id=playlist_id,
                spotify_track_id=t["id"],
                name=t.get("name", ""),
                duration_ms=t.get("duration_ms", 0),
                explicit=t.get("explicit", False),
                popularity=t.get("popularity", 0),
                preview_url=t.get("preview_url"),
                external_url=t.get("external_urls", {}).get("spotify", ""),
                album_name=album.get("name", ""),
                album_image_url=album_image,
                album_release_date=album.get("release_date"),
                track_number=t.get("track_number", 0),
                position=position,
                added_at=item.get("added_at"),
            )
            track.artists = artists
            track.genres = []  # Genre data requires separate artist API calls
            tracks.append(track)

        return tracks


# ── Singleton ──────────────────────────────────────────────────────────────
playlist_service = PlaylistService()
