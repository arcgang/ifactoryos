"""PostgreSQL connection pools — one pool per data store per process lifetime."""

from __future__ import annotations

import asyncpg

_pool: asyncpg.Pool | None = None
_ts_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        from app.config import settings

        _pool = await asyncpg.create_pool(settings.citus_url, min_size=2, max_size=10)
    return _pool


async def get_ts_pool() -> asyncpg.Pool:
    """Return the shared TimescaleDB connection pool (uns_telemetry hypertable)."""
    global _ts_pool
    if _ts_pool is None:
        from app.config import settings

        _ts_pool = await asyncpg.create_pool(settings.timescaledb_url, min_size=2, max_size=10)
    return _ts_pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def close_ts_pool() -> None:
    global _ts_pool
    if _ts_pool is not None:
        await _ts_pool.close()
        _ts_pool = None
