"""Database engines and sessions.

The API and the background worker use the async engine (asyncpg). Alembic
migrations and the offline scripts in tools/ keep a synchronous engine (psycopg2).
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings


def _database_url(driver: str, **query: str) -> URL:
    return URL.create(driver, username=settings.database_username, password=settings.database_password,
                      host=settings.database_hostname, port=int(settings.database_portname),
                      database=settings.database_name, query=query)


# Sync URL: Alembic (alembic/env.py) and tools/ connect through psycopg2.
DATABASE_URL = _database_url("postgresql", sslmode="require")
SQLALCHEMY_DATABASE_URL = DATABASE_URL.render_as_string(hide_password=False)

ASYNC_DATABASE_URL = _database_url("postgresql+asyncpg")
# asyncpg takes SSL and the connect timeout as arguments rather than URL parameters.
# Prepared-statement caching is off and statement names are unique, so the app also
# works through Supabase's transaction pooler (port 6543), which cannot keep a
# prepared statement between transactions. The cost is negligible at this scale.
ASYNC_CONNECT_ARGS = {
    "ssl": "require",
    "timeout": 5,
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
}

engine = create_async_engine(ASYNC_DATABASE_URL, pool_pre_ping=True, pool_recycle=1800, pool_timeout=5,
                             connect_args=ASYNC_CONNECT_ARGS)
# expire_on_commit=False: an async session cannot lazily reload attributes after a
# commit, so committed objects keep their in-memory values; refresh() when needed.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

sync_engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800, pool_timeout=5,
                            connect_args={"connect_timeout": 5})
SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)

Base = declarative_base()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def worker_session() -> AsyncIterator[AsyncSession]:
    """A session for one background job.

    Each RQ job runs in its own event loop (asyncio.run), and asyncpg connections
    cannot move between loops, so a job gets an unpooled engine it disposes of.
    """
    job_engine = create_async_engine(ASYNC_DATABASE_URL, poolclass=NullPool, connect_args=ASYNC_CONNECT_ARGS)
    try:
        async with async_sessionmaker(job_engine, expire_on_commit=False, autoflush=False)() as session:
            yield session
    finally:
        await job_engine.dispose()


async def reload_expired(db: AsyncSession, *entities) -> None:
    """Reload columns a rollback expired, keeping values assigned since then.

    The sync session reloaded an expired attribute silently on first read; an async
    session raises MissingGreenlet instead. Call this before reading a row that a
    rollback (for example a failed provider call in ExternalOperations) may have expired.
    """
    for entity in entities:
        state = inspect(entity)
        if not state.persistent:
            continue
        names = [attribute.key for attribute in state.mapper.column_attrs if attribute.key in state.unloaded]
        if names:
            await db.refresh(entity, attribute_names=names)


async def ping_db(*, check_schema=False) -> tuple[bool, str]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            if check_schema:
                await conn.execute(text("SELECT operation_key FROM external_operations LIMIT 0"))
                revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
                if revision != "f4a100000001":
                    return False, "Database schema upgrade required"
        return True, "DB connection OK"

    except (SQLAlchemyError, OSError) as e:
        return False, f"DB check failed ({e.__class__.__name__})"
