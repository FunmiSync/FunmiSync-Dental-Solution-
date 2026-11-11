"""Read-only check that this code can reach the configured database (e.g. Supabase).

Run from the project root with a filled-in .env:

    .venv/bin/python -m tools.check_database

It connects with the app's own async engine (asyncpg, SSL, pooler-safe options),
runs every query inside a READ ONLY transaction, and prints no secrets or row
contents. Exit code 0 means every check passed.
"""
import asyncio
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import ASYNC_DATABASE_URL, engine
from app.models.entities import AppointmentSyncLog, RegisteredClinics, RoleAssignment, Users, Wallet

HEAD_REVISION = "f4a100000001"  # newest file in alembic/versions

CONNECTION_HINTS = (
    ("password authentication failed", "Wrong user or password. Supabase pooler users look like postgres.<project-ref>."),
    ("tenant or user not found", "Pooler username must be postgres.<project-ref> (Supabase > Project Settings > Database)."),
    ("nodename nor servname", "Hostname not found. Copy the host from Supabase > Project Settings > Database."),
    ("name or service not known", "Hostname not found. Copy the host from Supabase > Project Settings > Database."),
    ("timeout", "No answer. Direct hosts (db.<ref>.supabase.co) are IPv6-only; try the pooler host instead."),
    ("connect call failed", "Connection refused. Check DATABASE_HOSTNAME and DATABASE_PORTNAME (5432 or 6543)."),
    ("ssl", "SSL problem. Supabase requires SSL; the app already asks for it."),
)

failures = 0


def report(ok: bool, label: str, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{f' - {detail}' if detail else ''}")


async def read_only(statement_or_callable):
    """Run one check on its own connection inside a READ ONLY transaction."""
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        if callable(statement_or_callable):
            return await statement_or_callable(connection)
        return await connection.scalar(statement_or_callable)


async def load_one_row(model):
    # Loading a full row through the ORM proves asyncpg can decode its UUID,
    # enum and timestamp columns, and that every mapped column exists.
    async def load(connection):
        async with AsyncSession(bind=connection) as session:
            return await session.scalar(select(model).limit(1))
    return await read_only(load)


async def main() -> int:
    print(f"Database: {ASYNC_DATABASE_URL.render_as_string(hide_password=True)}")

    try:
        version = await read_only(text("SHOW server_version"))
    except Exception as exc:
        message = str(exc).lower()
        hint = next((tip for needle, tip in CONNECTION_HINTS if needle in message), "")
        report(False, "connect", f"{type(exc).__name__}: {str(exc)[:200]}")
        if hint:
            print(f"        hint: {hint}")
        return 1
    report(True, "connect", f"PostgreSQL {version}, SSL required")

    if await read_only(text("SELECT to_regclass('public.alembic_version') IS NOT NULL")):
        revision = await read_only(text("SELECT version_num FROM alembic_version LIMIT 1"))
        report(revision == HEAD_REVISION, "migration revision",
               f"{revision}" + ("" if revision == HEAD_REVISION else f" (this branch needs {HEAD_REVISION}; see below)"))
    else:
        report(False, "migration revision", "no alembic_version table")

    journal = await read_only(text("SELECT to_regclass('public.external_operations') IS NOT NULL"))
    report(journal, "external_operations table (Phase 4)", "present" if journal else "missing")

    for model in (Users, RegisteredClinics, RoleAssignment, Wallet, AppointmentSyncLog):
        try:
            row = await load_one_row(model)
            report(True, f"read {model.__tablename__} through the ORM", "1 row decoded" if row else "table is empty")
        except Exception as exc:
            report(False, f"read {model.__tablename__} through the ORM", f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")

    await engine.dispose()
    if failures:
        print(f"\n{failures} check(s) failed.")
        if not journal:
            print("The Phase 4 migration has not been applied. Back up the database first, then run:\n"
                  "    .venv/bin/alembic upgrade head")
    else:
        print("\nAll checks passed: this branch can reach the database and read it with the async driver.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
