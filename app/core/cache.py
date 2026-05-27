"""
app/core/cache.py
─────────────────
Cache abstraction with two backends:
  • Redis  — for production / multi-worker deployments
  • InMemoryCache — for local dev without Redis (USE_REDIS=false)

Both implement the same async interface so the rest of the app
doesn't need to know which backend is active.
"""

import json
import time
from typing import Any
from loguru import logger
from app.core.config import get_settings

settings = get_settings()


class InMemoryCache:
    """Simple dict-based TTL cache — single process only, not distributed."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expiry_ts)

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.time() > expiry:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int = settings.cache_ttl_seconds) -> None:
        self._store[key] = (value, time.time() + ttl)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear_pattern(self, pattern: str) -> None:
        """Remove all keys containing pattern."""
        to_delete = [k for k in self._store if pattern in k]
        for k in to_delete:
            del self._store[k]


class RedisCache:
    """Redis-backed cache using the async redis-py client."""

    def __init__(self) -> None:
        self._client: Any = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis
        self._client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await self._client.ping()
        logger.info("Redis cache connected")

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl: int = settings.cache_ttl_seconds) -> None:
        await self._client.setex(key, ttl, json.dumps(value))

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def clear_pattern(self, pattern: str) -> None:
        keys = await self._client.keys(f"*{pattern}*")
        if keys:
            await self._client.delete(*keys)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()


# ── Module-level singleton ─────────────────────────────────────────────────
# Instantiated at import time; connection opened in app lifespan.
cache: InMemoryCache | RedisCache

if settings.use_redis:
    cache = RedisCache()
else:
    cache = InMemoryCache()
    logger.info("Using in-memory cache (USE_REDIS=false)")
