"""See each clinic's line of CRM events waiting for OpenDental, and wake the lines.

The webhook saves every CRM event in inbound_events and queues a job that wakes the
clinic's line; a worker then sends the clinic's events to OpenDental, oldest first
(app/infrastructure/workers/appointments.py). Nothing is lost if a job is: the events
stay in the database. `wake` queues a job for every clinic with waiting events, so a
lost job, a Redis restart or a dropped delayed job only delays them.

    .venv/bin/python -m tools.sync_line status    # read-only: waiting and dead events, open alerts
    .venv/bin/python -m tools.sync_line wake      # safe to run on a schedule (Render Cron Job)
    .venv/bin/python -m tools.sync_line replay --clinic <clinic id> --by "<your name>"
    .venv/bin/python -m tools.sync_line replay --event <event id> --by "<your name>"

An event that still fails after 5 retries goes to the dead queue: it is kept, never resent
on its own, and our team is alerted. Fix the cause, then `replay` puts it back in its
clinic's line with a fresh set of retries (recorded in the audit log).

The worker must run with the scheduler, which starts delayed jobs (retries, OpenDental pauses):
    rq worker appointments --with-scheduler --url "$REDIS_URL"
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.infrastructure.database import AsyncSessionLocal, engine
from app.infrastructure.workers import appointments as line
from app.models.entities import InboundEvent, RegisteredClinics, SyncAlert
from app.services.clinic_health import as_utc, opendental_paused_until


def format_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 120:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"


async def show_status() -> int:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(InboundEvent.clinic_id, InboundEvent.processing_status, func.count(), func.min(InboundEvent.received_at))
            .where(InboundEvent.processing_status.in_(line.WAITING + (line.DEAD,)))
            .group_by(InboundEvent.clinic_id, InboundEvent.processing_status)
            .order_by(InboundEvent.clinic_id))).all()
        alerts = (await db.scalars(select(SyncAlert).where(SyncAlert.resolved_at.is_(None))
                                   .order_by(SyncAlert.audience.desc(), SyncAlert.created_at))).all()
        clinic_ids = {row[0] for row in rows} | {alert.clinic_id for alert in alerts}
        clinics = {clinic.id: clinic for clinic in (await db.scalars(
            select(RegisteredClinics).where(RegisteredClinics.id.in_(clinic_ids)))).all()} if clinic_ids else {}
    if not rows:
        print("No CRM events are waiting.")
    for clinic_id in sorted({row[0] for row in rows}, key=str):
        clinic = clinics.get(clinic_id)
        name = clinic.clinic_name if clinic else "(clinic missing)"
        paused = opendental_paused_until(clinic, now) if clinic else None
        note = f"  OpenDental paused until {paused:%d %b %H:%M} UTC" if paused else ""
        note += "  (disabled: kept until enabled)" if clinic and clinic.is_disabled else ""
        print(f"{name}  {clinic_id}{note}")
        for _, status, count, oldest in (row for row in rows if row[0] == clinic_id):
            age = format_age((now - as_utc(oldest)).total_seconds())
            hint = "   resend: replay --clinic <id> --by <name>" if status == line.DEAD else ""
            print(f"  {status:<15} {count:>5}   oldest {age}{hint}")
    if alerts:
        print("\nOpen alerts (internal: for our team; clinic: the clinic was emailed and notified)")
        for alert in alerts:
            name = clinics[alert.clinic_id].clinic_name if alert.clinic_id in clinics else alert.clinic_id
            age = format_age((now - as_utc(alert.created_at)).total_seconds())
            email = "" if alert.audience != "clinic" else (
                "  emailed" if alert.emailed_at else f"  NOT emailed: {alert.email_error or 'pending'}")
            print(f"  {alert.audience:<8} {name}  {alert.code}  open {age}{email}\n           {alert.message}")
    return 0


async def wake_all() -> int:
    async with AsyncSessionLocal() as db:
        clinic_ids = await line.wake_waiting_clinics(db)
    print(f"Queued a job for {len(clinic_ids)} clinic line(s).")
    return 0


async def replay(args) -> int:
    async with AsyncSessionLocal() as db:
        count = await line.replay_dead_events(db, by=args.by, clinic_id=args.clinic, event_id=args.event)
    print(f"Resent {count} event(s) from the dead queue." if count else "No dead events matched.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="See and wake each clinic's line of CRM events.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="waiting and dead events per clinic, open alerts (read-only)")
    commands.add_parser("wake", help="queue a job for every enabled clinic with waiting events")
    resend = commands.add_parser("replay", help="resend events from the dead queue once the cause is fixed")
    target = resend.add_mutually_exclusive_group(required=True)
    target.add_argument("--clinic", type=UUID, help="every dead event of this clinic")
    target.add_argument("--event", type=UUID, help="one dead event")
    resend.add_argument("--by", required=True, help="who is resending (kept in the audit log)")
    args = parser.parse_args(argv)
    if args.command == "replay" and not args.by.strip():
        parser.error("--by must name who is resending")

    async def run():
        try:
            if args.command == "status":
                return await show_status()
            if args.command == "wake":
                return await wake_all()
            return await replay(args)
        finally:
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
