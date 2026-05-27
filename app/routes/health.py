from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from app.database.session import get_db

router = APIRouter(tags=["Health"])

@router.get("/", summary="Root")
async def root() -> dict:
    return {"service": "Playlist-as-a-Service", "docs": "/docs", "health": "/health"}

@router.get("/health", summary="Health check")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "timestamp": datetime.now(timezone.utc).isoformat()}