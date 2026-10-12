"""PostgreSQL connection pool — one pool per process lifetime."""

from __future__ import annotations

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        from app.config import get_settings

        cfg = get_settings()
        _pool = await asyncpg.create_pool(cfg.citus_url, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
