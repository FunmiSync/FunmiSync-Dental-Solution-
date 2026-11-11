"""Create users, DSOs and clinics, each with its owner membership, atomically."""
import logging
import secrets
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.infrastructure.cache.dso_clinic_page_cache import invalidate_dso_clinic_list_cache
from app.models.entities import Dso, RegisteredClinics, RoleAssignment, RoleType, ScopeType, Users
from app.schemas.api import cliniccreate
from app.security.encryption import encrypt_secret
from app.security.oauth2 import hashpassword
from app.services.audit_logs import record_audit
from app.services.errors import InvalidRequest, NotFound, OperationFailed

logger = logging.getLogger(__name__)


class RegistrationConflict(InvalidRequest):
    """The account, DSO or clinic being registered already exists (HTTP 400)."""


# ---------------------------------------------------------------- users

async def create_user(db: AsyncSession, *, username: str, email: str, password: str) -> Users:
    email = email.strip().lower()
    try:
        return await _insert_user(db, username=username, email=email, password=password)
    except SQLAlchemyError as exc:
        logger.error("User registration failed (%s)", type(exc).__name__)
        raise OperationFailed("Unable to register user") from exc


async def _insert_user(db: AsyncSession, *, username: str, email: str, password: str) -> Users:
    if await db.scalar(select(Users.id).where(Users.email == email).limit(1)):
        raise RegistrationConflict("User with this email already exist")
    # bcrypt is ~0.25 s of CPU; keep it off the event loop.
    user = Users(username=username, email=email, password=await run_in_threadpool(hashpassword, password))
    try:
        db.add(user)
        await db.flush()
        record_audit(db, entity_type="user", entity_id=user.id, action="register")
        await db.commit()
        await db.refresh(user)
        return user
    except IntegrityError as exc:
        await db.rollback()
        raise RegistrationConflict("User with this email already exist") from exc
    except Exception:
        await db.rollback()
        raise


async def create_google_user(db: AsyncSession, *, username: str, google_user: dict) -> Users:
    if await db.scalar(select(Users.id).where(Users.google_sub == google_user["sub"]).limit(1)):
        logger.warning("Google registration rejected: Google account already exists", extra={"email": google_user["email"]})
        raise RegistrationConflict("Google account already registered. Use login with Google.")

    if await db.scalar(select(Users.id).where(Users.email == google_user["email"]).limit(1)):
        logger.warning("Google registration rejected: email already exists", extra={"email": google_user["email"]})
        raise RegistrationConflict("Email already exists. Sign in normally ")

    user = Users(
        username=username,
        email=google_user["email"],
        # Google users sign in with Google only; this password can never be typed.
        password=await run_in_threadpool(hashpassword, secrets.token_urlsafe(48)),
        google_sub=google_user["sub"],
        auth_provider="google",
    )
    try:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Google user registration failed during database write", extra={"email": google_user["email"]})
        raise OperationFailed("Failed to register user") from exc

    logger.info("Google user registered successfully", extra={"user_id": str(user.id), "email": user.email})
    return user


# ----------------------------------------------------------------- DSOs

async def create_dso(db: AsyncSession, *, user_id: UUID, name: str) -> Dso:
    try:
        return await _insert_dso(db, user_id=user_id, name=name)
    except SQLAlchemyError as exc:
        logger.error("DSO registration failed (%s)", type(exc).__name__)
        raise OperationFailed("Unable to register DSO") from exc


async def _insert_dso(db: AsyncSession, *, user_id: UUID, name: str) -> Dso:
    if await db.scalar(select(Dso.id).where(Dso.user_id == user_id).limit(1)):
        raise RegistrationConflict("This Dso already exist")
    try:
        dso = Dso(name=name, user_id=user_id)
        db.add(dso)
        await db.flush()
        db.add(RoleAssignment(user_id=user_id, scope_type=ScopeType.DSO, dso_id=dso.id,
                              role=RoleType.ADMIN, created_by=user_id))
        record_audit(db, entity_type="dso", entity_id=dso.id, action="create", details={"actor_user_id": str(user_id)})
        await db.commit()
        await db.refresh(dso)
        return dso
    except IntegrityError as exc:
        await db.rollback()
        raise RegistrationConflict("This Dso already exist") from exc
    except Exception:
        await db.rollback()
        raise


async def get_dso(db: AsyncSession, *, dso_id: UUID) -> Dso:
    dso = await db.scalar(select(Dso).where(Dso.id == dso_id).limit(1))
    if not dso:
        raise NotFound("Dso Not Found")
    return dso


# -------------------------------------------------------------- clinics

async def register_standalone_clinic(db: AsyncSession, *, payload: cliniccreate, owner_id: UUID, request_id: str | None = None) -> RegisteredClinics:
    existing = await db.scalar(select(RegisteredClinics.id).where(
        RegisteredClinics.owner_id == owner_id,
        RegisteredClinics.clinic_name == payload.clinic_name,
        RegisteredClinics.dso_id.is_(None),
    ).limit(1))
    if existing:
        logger.warning("Duplicate clinic creation attempt",
                       extra={"user_id": owner_id, "clinic_name": payload.clinic_name, "request_id": request_id})
        raise RegistrationConflict("You Already have a clinic created already")

    clinic = await _save_clinic_with_admin(
        db,
        _new_clinic(payload, owner_id=owner_id),
        admin_user_id=owner_id,
        log_extra={"user_id": owner_id, "request_id": request_id, "clinic_name": payload.clinic_name},
    )
    logger.info("Standalone clinic created",
                extra={"user_id": owner_id, "clinic_name": clinic.clinic_name, "owner_id": owner_id})
    return clinic


async def register_dso_clinic(db: AsyncSession, *, dso: Dso, payload: cliniccreate, actor_user_id: UUID, request_id: str | None = None) -> RegisteredClinics:
    existing = await db.scalar(select(RegisteredClinics.id).where(
        RegisteredClinics.dso_id == dso.id,
        RegisteredClinics.clinic_number == payload.clinic_number,
    ).limit(1))
    if existing:
        raise RegistrationConflict("Clinic already exist in DSO")

    # The DSO owner owns the clinic; the person who created it becomes its admin.
    clinic = await _save_clinic_with_admin(
        db,
        _new_clinic(payload, owner_id=dso.user_id, dso_id=dso.id),
        admin_user_id=actor_user_id,
        log_extra={"user_id": actor_user_id, "request_id": request_id, "clinic_name": payload.clinic_name, "dso_id": dso.id},
    )
    await invalidate_dso_clinic_list_cache(dso_id=dso.id)
    logger.info("DSO clinic created",
                extra={"user_id": actor_user_id, "clinic_name": clinic.clinic_name, "dso_id": dso.id, "owner_id": dso.user_id})
    return clinic


def _new_clinic(payload: cliniccreate, *, owner_id: UUID, dso_id: UUID | None = None) -> RegisteredClinics:
    # Provider credentials and the webhook secret are stored encrypted only.
    return RegisteredClinics(
        crm_type=payload.crm_type,
        clinic_name=payload.clinic_name,
        clinic_number=payload.clinic_number,
        clinic_timezone=payload.clinic_timezone,
        od_developer_key=encrypt_secret(payload.od_developer_key),
        od_customer_key=encrypt_secret(payload.od_customer_key),
        crm_api_key=encrypt_secret(payload.crm_api_key),
        webhook_secret=encrypt_secret(secrets.token_urlsafe(32)),
        location_id=payload.location_id,
        calendar_id=encrypt_secret(payload.calendar_id),
        operatory_calendar_map={
            status: [item.model_dump() for item in items]
            for status, items in payload.operatory_calendar_map.items()
        },
        owner_id=owner_id,
        dso_id=dso_id,
    )


async def _save_clinic_with_admin(db: AsyncSession, clinic: RegisteredClinics, *, admin_user_id: UUID, log_extra: dict) -> RegisteredClinics:
    try:
        db.add(clinic)
        await db.flush()
        db.add(RoleAssignment(
            user_id=admin_user_id,
            scope_type=ScopeType.CLINIC,
            clinic_id=clinic.id,
            role=RoleType.ADMIN,
            created_by=admin_user_id,
            is_active=True,
        ))
        await db.commit()
        await db.refresh(clinic)
        return clinic
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Database error while creating clinic", extra=log_extra)
        raise OperationFailed("Unable to create clinic at this time") from exc
