"""
app/core/config.py
──────────────────
Central configuration using pydantic-settings.
All values are read from environment variables (or .env file).
This single source of truth prevents scattered magic strings.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me"

    # ── Spotify ────────────────────────────────────────────────────────────
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_token_url: str = "https://accounts.spotify.com/api/token"
    spotify_api_base_url: str = "https://api.spotify.com/v1"

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./playlist_service.db"

    # ── Redis / Cache ──────────────────────────────────────────────────────
    use_redis: bool = False
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600

    # ── Rate Limiting ──────────────────────────────────────────────────────
    rate_limit_per_minute: int = 60

    # ── Logging ────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """
    Cached singleton — imported throughout the app via dependency injection.
    lru_cache ensures .env is only parsed once per process lifetime.
    """
    return Settings()
