"""One clinic workflow at a time across processes and local DB commits."""
from contextlib import asynccontextmanager
import hashlib
from sqlalchemy import text


class ClinicBusyError(ConnectionError):
    pass


@asynccontextmanager
async def clinic_sync_lock(db, clinic_id):
    # Transaction locks would be released by service-level commits. A dedicated
    # connection holds a session lock for the full external workflow instead.
    bind = db.bind
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Clinic synchronization requires PostgreSQL advisory locks")
    key = int.from_bytes(hashlib.sha256(f"clinic-sync:{clinic_id}".encode()).digest()[:8], "big", signed=True)
    connection = await bind.connect()
    acquired = False
    try:
        acquired = bool((await connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})).scalar_one())
        await connection.commit()
        if not acquired:
            raise ClinicBusyError("Clinic synchronization is already processing another event")
        yield
    finally:
        try:
            if acquired:
                try:
                    await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    await connection.commit()
                except BaseException:
                    # Never put a connection still holding a session lock back
                    # into the pool. Closing an invalidated connection releases it.
                    await connection.invalidate()
                    raise
        finally:
            await connection.close()
