"""Alerts for CRM events that cannot reach OpenDental, and who hears about them.

A problem is announced once. Events with the same code and subject share one open
alert, so 50 events failing for one reason, or one event retried every hour, raise one
alert. A clinic alert emails the clinic manager at once and notifies every team member
in the app; it also shows in the DSO clinic list and on the event's sync log. An
internal alert is for our team: it is logged as an error and listed by tools.sync_line.
An alert closes by itself when an event goes through and no other event is still waiting
on it: the cause is evidently fixed. Events in the dead queue do not hold it open; they are
our team's dead-queue alert, which closes once the clinic's dead queue is empty (replayed,
or replaced by newer changes). Until something goes through, an alert stays open.
"""
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.infrastructure.cache.dso_clinic_page_cache import invalidate_dso_clinic_list_cache
from app.infrastructure.email import send_email
from app.models.entities import (
    InboundEvent, RegisteredClinics, RoleAssignment, RoleType, ScopeType, SyncAlert, UserNotification, Users,
)

logger = logging.getLogger(__name__)

CLINIC, INTERNAL = "clinic", "internal"
DEAD_QUEUE = "dead_queue"
# Events in these statuses keep their alert open (the worker's waiting statuses).
STILL_WAITING = ("queued", "waiting", "retrying", "needs_attention", "processing")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def raise_alert(db, clinic: RegisteredClinics, *, audience: str, code: str, message: str,
                      subject: str = "") -> SyncAlert:
    """Open an alert for this problem, or return the one already open. Commits.

    Only a new clinic alert notifies the team; the manager's email is tried again on later
    sightings until it has gone out (for example once email is set up)."""
    alert = await _open_alert(db, clinic.id, code, subject)
    created = alert is None
    if created:
        alert = SyncAlert(id=uuid4(), clinic_id=clinic.id, audience=audience, code=code, subject_key=subject,
                          message=message)
        db.add(alert)
        try:
            await db.commit()
        except IntegrityError:
            # Opened in the meantime (the partial unique index allows one open alert per problem).
            await db.rollback()
            alert, created = await _open_alert(db, clinic.id, code, subject), False
    if not created:
        alert.last_seen_at = _now()
        alert.message = message
        await db.commit()
        return await _email_if_needed(db, clinic, alert)

    if audience == INTERNAL:
        logger.error("CRM events need our team", extra={"clinic_id": str(clinic.id), "code": code,
                                                        "alert_id": str(alert.id), "detail": message})
    else:
        await _notify_team(db, clinic, alert)
    await _changed(clinic)
    return await _email_if_needed(db, clinic, alert)


async def resolve_alert(db, clinic: RegisteredClinics, alert: SyncAlert) -> None:
    if alert.resolved_at is None:
        alert.resolved_at = _now()
        await db.commit()
        await _changed(clinic)


async def resolve_if_nothing_waits(db, clinic: RegisteredClinics, alert_id) -> None:
    """Close the alert when none of its events is still waiting."""
    if alert_id is None:
        return
    alert = await db.get(SyncAlert, alert_id)
    if alert is None or alert.resolved_at is not None:
        return
    waiting = await db.scalar(select(exists().where(
        InboundEvent.alert_id == alert_id, InboundEvent.processing_status.in_(STILL_WAITING))))
    if not waiting:
        await resolve_alert(db, clinic, alert)


async def resolve_dead_queue_if_empty(db, clinic: RegisteredClinics) -> None:
    """Close our team's dead-queue alert once none of the clinic's events is dead."""
    dead = await db.scalar(select(exists().where(
        InboundEvent.clinic_id == clinic.id, InboundEvent.processing_status == "dead")))
    if not dead:
        await resolve_codes(db, clinic, (DEAD_QUEUE,))


async def resolve_codes(db, clinic: RegisteredClinics, codes) -> None:
    """Close the clinic's open alerts of these kinds, e.g. once OpenDental answers again."""
    for alert in (await db.scalars(select(SyncAlert).where(
            SyncAlert.clinic_id == clinic.id, SyncAlert.code.in_(codes), SyncAlert.resolved_at.is_(None)))).all():
        await resolve_alert(db, clinic, alert)


async def open_clinic_alerts(db, clinic_ids) -> dict:
    """For the DSO clinic list: {clinic_id: (number of open clinic alerts, oldest message)}."""
    if not clinic_ids:
        return {}
    rows = (await db.execute(
        select(SyncAlert.clinic_id, SyncAlert.message, func.count().over(partition_by=SyncAlert.clinic_id))
        .where(SyncAlert.clinic_id.in_(clinic_ids), SyncAlert.audience == CLINIC, SyncAlert.resolved_at.is_(None))
        .order_by(SyncAlert.clinic_id, SyncAlert.created_at))).all()
    found = {}
    for clinic_id, message, count in rows:
        found.setdefault(clinic_id, (count, message))
    return found


# ------------------------------------------------------------------ who hears

async def _open_alert(db, clinic_id, code, subject) -> SyncAlert | None:
    return await db.scalar(select(SyncAlert).where(
        SyncAlert.clinic_id == clinic_id, SyncAlert.code == code, SyncAlert.subject_key == subject,
        SyncAlert.resolved_at.is_(None)).limit(1).execution_options(populate_existing=True))


async def clinic_team(db, clinic: RegisteredClinics) -> list[Users]:
    """Everyone on the clinic's team (any role), plus its owner; active users only."""
    members = (await db.scalars(select(Users).join(RoleAssignment, RoleAssignment.user_id == Users.id).where(
        RoleAssignment.scope_type == ScopeType.CLINIC, RoleAssignment.clinic_id == clinic.id,
        RoleAssignment.is_active.is_(True), Users.is_active.is_(True)))).unique().all()
    owner = await db.get(Users, clinic.owner_id)
    team = {user.id: user for user in members}
    if owner is not None and owner.is_active:
        team.setdefault(owner.id, owner)
    return list(team.values())


async def clinic_managers(db, clinic: RegisteredClinics) -> list[Users]:
    """Who is emailed: the clinic's managers; if it has none, its admins; else its owner."""
    for role in (RoleType.MANAGER, RoleType.ADMIN):
        users = (await db.scalars(select(Users).join(RoleAssignment, RoleAssignment.user_id == Users.id).where(
            RoleAssignment.scope_type == ScopeType.CLINIC, RoleAssignment.clinic_id == clinic.id,
            RoleAssignment.role == role, RoleAssignment.is_active.is_(True), Users.is_active.is_(True)))).unique().all()
        if users:
            return list(users)
    owner = await db.get(Users, clinic.owner_id)
    return [owner] if owner is not None and owner.is_active else []


async def _notify_team(db, clinic: RegisteredClinics, alert: SyncAlert) -> None:
    for user in await clinic_team(db, clinic):
        db.add(UserNotification(id=uuid4(), user_id=user.id, alert_id=alert.id))
    await db.commit()


async def _email_if_needed(db, clinic: RegisteredClinics, alert: SyncAlert) -> SyncAlert:
    if alert.audience != CLINIC or alert.emailed_at is not None:
        return alert
    managers = await clinic_managers(db, clinic)
    if not managers:
        alert.email_error = "Nobody to email: the clinic has no manager, admin or active owner"
        await db.commit()
        return alert
    subject = f"Action needed: CRM changes for {clinic.clinic_name} are not reaching OpenDental"
    body = (f"{alert.message}\n\n"
            "Every change waiting for this is kept, and sent to OpenDental automatically once it is fixed. "
            "We try again every hour.\n\n"
            f"Open FunmiSync: {settings.frontend_base_url}\n")
    errors = []
    for manager in managers:
        try:
            await send_email(to=manager.email, subject=subject, body=body)
        except Exception as exc:  # an email failure must never stop the line
            errors.append(type(exc).__name__ if not str(exc) else str(exc))
    if len(errors) < len(managers):
        alert.emailed_at = _now()
    alert.email_error = "; ".join(sorted(set(errors))) or None
    if errors:
        logger.warning("Could not email every clinic manager about a sync alert",
                       extra={"clinic_id": str(clinic.id), "alert_id": str(alert.id), "error": alert.email_error})
    await db.commit()
    return alert


async def _changed(clinic: RegisteredClinics) -> None:
    if clinic.dso_id:
        await invalidate_dso_clinic_list_cache(dso_id=clinic.dso_id)

