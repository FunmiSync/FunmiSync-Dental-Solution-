"""SQLite unit fixture for durable state transitions, never a PostgreSQL substitute."""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy import Boolean, MetaData, event, text
from sqlalchemy.schema import DefaultClause
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.entities import Base


def sqlite_metadata() -> MetaData:
    """The app's tables, adjusted where SQLite would otherwise behave unlike PostgreSQL."""
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        copied = table.to_metadata(metadata)
        # SQLite can exercise the predicate invariant, but its locks/isolation
        # differ. Dedicated PostgreSQL concurrency tests are still required.
        for index in copied.indexes:
            where = index.dialect_options["postgresql"].get("where")
            if where is not None:
                index.dialect_options["sqlite"]["where"] = where
        # PostgreSQL reads server_default "false" as the boolean false; SQLite would store
        # the text 'false', which reads back as True. Use 0/1 so defaults mean the same.
        for column in copied.columns:
            default = column.server_default
            if isinstance(column.type, Boolean) and default is not None and str(getattr(default, "arg", "")).lower() in ("true", "false"):
                column.server_default = DefaultClause(text("1" if str(default.arg).lower() == "true" else "0"))
    return metadata


async def local_session():
    """An AsyncSession on a private in-memory SQLite database, configured like the app's."""
    # StaticPool keeps the single in-memory connection, so every query sees the same tables.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def configure(connection, _):
        connection.create_function("gen_random_uuid", 0, lambda: uuid4().hex)

    metadata = sqlite_metadata()
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)()
    return session, engine


async def close_local_session(session, engine):
    await session.close()
    await engine.dispose()


def fake_async_session(*, scalar=None, scalars=(), rows=()):
    """A MagicMock standing in for AsyncSession in unit tests that avoid a database.

    `scalar` is what `await db.scalar(...)` returns; `scalars` and `rows` back
    `(await db.scalars(...)).all()` and `(await db.execute(...)).all()`.
    """
    db = MagicMock(name="AsyncSession")
    for name in ("commit", "rollback", "refresh", "flush", "close", "delete", "get"):
        setattr(db, name, AsyncMock(name=name))
    db.scalar = AsyncMock(name="scalar", return_value=scalar)
    scalar_result = MagicMock(name="ScalarResult")
    scalar_result.all.return_value = list(scalars)
    scalar_result.first.return_value = next(iter(scalars), None)
    db.scalars = AsyncMock(name="scalars", return_value=scalar_result)
    result = MagicMock(name="Result")
    result.all.return_value = list(rows)
    result.first.return_value = next(iter(rows), None)
    result.scalar_one.return_value = scalar
    db.execute = AsyncMock(name="execute", return_value=result)
    return db


def local_sync_session():
    """A synchronous SQLite session for code that stays sync (tools/ CLI scripts)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.create_function("gen_random_uuid", 0, lambda: uuid4().hex)

    metadata = sqlite_metadata()
    metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=True)(), engine
