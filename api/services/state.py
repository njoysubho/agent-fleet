from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.db.connection import create_engine
from shared.redis_client import RedisCoordinator


@dataclass
class AppState:
    coordinator: RedisCoordinator
    db_engine: AsyncEngine | None


async def build_state() -> AppState:
    coordinator = RedisCoordinator(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    db_url = os.environ.get("DATABASE_URL")
    engine: AsyncEngine | None = None
    if db_url:
        engine = create_engine(db_url)
    return AppState(coordinator=coordinator, db_engine=engine)


async def check_db(engine: AsyncEngine | None) -> bool:
    if engine is None:
        return True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
