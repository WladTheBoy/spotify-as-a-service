"""
app/services/spotify.py
───────────────────────
Spotify Web API client using the Client Credentials flow.

Architecture decision: this service is stateless — it doesn't touch the DB.
It only knows how to talk to Spotify. The playlist service (below) owns
the orchestration between Spotify, cache, and the database.

Client Credentials flow:
  POST /api/token with (client_id, client_secret) → access_token (1 hour TTL)
  All subsequent API calls use Bearer <access_token>
"""

import re
import base64
import time
from typing import Any
import httpx
from loguru import logger
from app.core.config import get_settings
from app.core.cache import cache

settings = get_settings()

# Token cache key
_TOKEN_CACHE_KEY = "spotify:access_token"
_TOKEN_EXPIRY_KEY = "spotify:token_expiry"

# In-memory token store (avoids cache round-trip for every request)
_token_store: dict[str, Any] = {"token": None, "expires_at": 0.0}


class SpotifyError(Exception):
    """Raised when Spotify returns an unexpected error response."""
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpotifyService:
    """
    Async Spotify API client.
    Instantiated once and reused — shares the HTTPX connection pool.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Authentication ─────────────────────────────────────────────────────

    async def get_access_token(self) -> str:
        """
        Fetch a Spotify access token, reusing the cached one if still valid.
        Tokens last 3600s; we refresh 60s early to avoid edge-case expiry.
        """
        now = time.time()
        if _token_store["token"] and _token_store["expires_at"] > now + 60:
            return str(_token_store["token"])

        logger.info("Fetching new Spotify access token")
        credentials = f"{settings.spotify_client_id}:{settings.spotify_client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()

        client = await self._get_client()
        response = await client.post(
            settings.spotify_token_url,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )

        if response.status_code != 200:
            raise SpotifyError(
                f"Failed to obtain Spotify token: {response.text}",
                status_code=response.status_code,
            )

        data = response.json()
        token: str = data["access_token"]
        expires_in: int = data.get("expires_in", 3600)

        _token_store["token"] = token
        _token_store["expires_at"] = now + expires_in

        logger.info(f"Spotify token obtained, valid for {expires_in}s")
        return token

    async def _auth_headers(self) -> dict[str, str]:
        token = await self.get_access_token()
        return {"Authorization": f"Bearer {token}"}

    # ── URL / ID helpers ────────────────────────────────────────────────────

    @staticmethod
    def extract_playlist_id(url: str) -> str:
        """
        Extract the Spotify playlist ID from various URL formats:
          https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
          https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=xyz
          spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
        """
        patterns = [
            r"open\.spotify\.com/playlist/([A-Za-z0-9]+)",
            r"spotify:playlist:([A-Za-z0-9]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        raise SpotifyError(f"Could not extract playlist ID from URL: {url}", status_code=400)

    # ── Playlist fetching ──────────────────────────────────────────────────

    async def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        """Fetch playlist metadata (no tracks) from Spotify."""
        cache_key = f"spotify:playlist:{playlist_id}"
        cached = await cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for playlist {playlist_id}")
            return cached  # type: ignore[return-value]

        client = await self._get_client()
        headers = await self._auth_headers()
        url = f"{settings.spotify_api_base_url}/playlists/{playlist_id}"
        params = {"fields": "id,name,description,owner,images,followers,public,tracks.total,external_urls"}

        logger.info(f"Fetching Spotify playlist {playlist_id}")
        response = await client.get(url, headers=headers, params=params)
        self._raise_for_status(response)

        data: dict[str, Any] = response.json()
        await cache.set(cache_key, data)
        return data

    async def get_playlist_tracks(self, playlist_id: str) -> list[dict[str, Any]]:
        """
        Fetch ALL tracks from a playlist, handling Spotify's 100-item pagination.
        Returns a flat list of track item dicts.
        """
        cache_key = f"spotify:tracks:{playlist_id}"
        cached = await cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for tracks of playlist {playlist_id}")
            return cached  # type: ignore[return-value]

        client = await self._get_client()
        headers = await self._auth_headers()
        url = f"{settings.spotify_api_base_url}/playlists/{playlist_id}/tracks"
        fields = (
            "items(added_at,track(id,name,duration_ms,explicit,popularity,"
            "preview_url,external_urls,album(name,images,release_date),"
            "artists(id,name))),next,total,offset,limit"
        )

        all_tracks: list[dict[str, Any]] = []
        offset = 0
        limit = 100  # Spotify's maximum per page

        while True:
            params = {"fields": fields, "limit": limit, "offset": offset}
            logger.debug(f"Fetching tracks offset={offset}")
            response = await client.get(url, headers=headers, params=params)
            self._raise_for_status(response)

            page: dict[str, Any] = response.json()
            items: list[dict[str, Any]] = page.get("items", [])
            # Filter out null tracks (can happen with removed Spotify tracks)
            all_tracks.extend([i for i in items if i.get("track") and i["track"].get("id")])

            if page.get("next") is None:
                break
            offset += limit

        logger.info(f"Fetched {len(all_tracks)} tracks for playlist {playlist_id}")
        await cache.set(cache_key, all_tracks)
        return all_tracks

    # ── Error handling ─────────────────────────────────────────────────────

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        if response.status_code == 401:
            raise SpotifyError("Spotify authentication failed — check credentials", 401)
        if response.status_code == 403:
            raise SpotifyError("Spotify access forbidden — playlist may be private", 403)
        if response.status_code == 404:
            raise SpotifyError("Playlist not found on Spotify", 404)
        if response.status_code == 429:
            raise SpotifyError("Spotify rate limit exceeded — please retry later", 429)
        raise SpotifyError(
            f"Spotify API error {response.status_code}: {response.text}",
            status_code=response.status_code,
        )


# ── Module-level singleton ─────────────────────────────────────────────────
spotify_service = SpotifyService()
