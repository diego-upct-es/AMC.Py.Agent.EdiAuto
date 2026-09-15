"""
Cliente PostgreSQL async.
Equivale a IOMDBConnectionFactory + Dapper en los microservicios .NET de AMC.

Dos tipos de operaciones:
  - fetch / fetchrow / fetchval  → lectura
  - execute / executemany        → escritura (solo CRUD para el usuario de aplicación)
"""

import asyncpg
import structlog
from app.config import get_settings

logger = structlog.get_logger()

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=settings.db_pool_size,
            command_timeout=30,
            max_inactive_connection_lifetime=300,
        )
        logger.info("PostgreSQL pool created", pool_size=settings.db_pool_size)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


async def fetch(query: str, *args) -> list[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args) -> asyncpg.Record | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def executemany(query: str, args: list) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(query, args)
