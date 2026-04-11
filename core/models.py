from sqlalchemy import Column, String, Integer, DateTime, Text, func, ForeignKey, UniqueConstraint, Boolean, Enum, text
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSON, UUID
from typing import Any, Optional
from core.database import Base
from datetime import datetime
import uuid
import enum


class Autoid():
    @declared_attr
    def id(cls):
        return Column(
            UUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
            index=True,
            unique=True,
            nullable=False,
        )



class ScopeType(str, enum.Enum):
    DSO = "dso"
    CLINIC = "clinic"


class RoleType(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    STAFF = "staff"


class SyncDirection(str, enum.Enum):
    CRM_TO_OD = "crm_to_od"
    OD_TO_CRM = "od_to_crm"


class SyncStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    PROCESSED = "processed"
    FAILED = "failed"


class Users(Base, Autoid):
    __tablename__ = "users"
    username = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password = Column(String, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    refresh_jti: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    clinics = relationship("RegisteredClinics", back_populates="owner",foreign_keys="RegisteredClinics.owner_id", cascade="all, delete")
    dsos = relationship("Dso", back_populates="user", cascade="all, delete")


class Dso(Base, Autoid):
    __tablename__ = "Dsos"
    name: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    clinics = relationship("RegisteredClinics", back_populates="dso", cascade="all, delete")
    user = relationship("Users", back_populates="dsos")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_dso_user_id"),
    )


class RegisteredClinics(Base, Autoid):
    __tablename__ = "registered_clinics"
    crm_type: Mapped[str] = mapped_column(String, nullable=False)
    clinic_name: Mapped[str] = mapped_column(String, nullable=False)
    clinic_number: Mapped[int] = mapped_column(Integer, nullable=False)
    clinic_timezone: Mapped[str] = mapped_column(String, nullable=False)
    od_developer_key: Mapped[str] = mapped_column(String, nullable=False)
    od_customer_key: Mapped[str] = mapped_column(String, nullable=False)
    crm_api_key: Mapped[str] = mapped_column(String, nullable=False)
    webhook_secret: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location_id: Mapped[str] = mapped_column(String, nullable=False)
    calendar_id: Mapped[str] = mapped_column(String, nullable=False)
    operatory_calendar_map: Mapped[Optional[dict[str, list[dict[str, Any]]]]] = mapped_column(JSON, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    dso_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete="CASCADE"), nullable=True, index=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable= False, default=False, server_default="false", index=True)    
    disabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True),ForeignKey("users.id", ondelete="SET NULL"),nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    dso = relationship("Dso", back_populates="clinics")
    owner = relationship("Users", back_populates="clinics", foreign_keys=[owner_id])
    patients = relationship("Patients", back_populates="clinic", cascade="all, delete")
    appointments = relationship("Appointments", back_populates="clinic", cascade="all, delete")


class Patients(Base, Autoid):
    __tablename__ = "patients"
    FName = Column(String, nullable=True)
    LName = Column(String, nullable=True)
    Gender = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=True)
    pat_num: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contact_id = Column(String, nullable=False)
    contact_id_hash = Column(String, nullable=True, index=True)
    clinic_id = Column(UUID(as_uuid=True), ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    clinic = relationship("RegisteredClinics", back_populates="patients")
    appointments = relationship("Appointments", back_populates="patient", cascade="all, delete")

    __table_args__ = (
        UniqueConstraint("clinic_id", "pat_num", name="uq_clinic_patnum"),
        UniqueConstraint("clinic_id", "contact_id_hash", name="uq_clinic_contactid_hash"),
    )


class Appointments(Base, Autoid):
    __tablename__ = "appointments"
    AptNum: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    previous_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    start_time: Mapped[str] = mapped_column(String, nullable=False)
    end_time: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)
    calendar_id: Mapped[str] = mapped_column(String, nullable=False)
    commslog_done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    popups_done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    clinic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable=False)
    pat_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    clinic = relationship("RegisteredClinics", back_populates="appointments", cascade="all, delete")
    patient = relationship("Patients", back_populates="appointments", cascade="all, delete")

    __table_args__ = (
        UniqueConstraint("clinic_id", "AptNum", name="uq_clinic_AptNum"),
        UniqueConstraint("clinic_id", "event_id", name="uq_clinic_eventid"),
    )


class InboundEvent(Base, Autoid):
    __tablename__ = "inbound_events"

    clinic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable=False)
    crm_type: Mapped[str] = mapped_column(String, nullable=False)
    event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    job_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    processing_status: Mapped[str] = mapped_column(String, nullable=False, default="received", server_default="received",)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0",)
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(),)
    processed_at: Mapped[Optional[datetime]] = mapped_column( DateTime(timezone=True), nullable=True,)


class Audit_logs(Base, Autoid):
    __tablename__ = "audit_logs"
    clinic_id = Column(UUID(as_uuid=True), nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    status = Column(String, nullable=False)
    source = Column(String, nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RoleAssignment(Base, Autoid):
    __tablename__ = "role_assignments"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope_type: Mapped[ScopeType] = mapped_column(Enum(ScopeType, name="scope_type_enum"), nullable=False)
    role: Mapped[RoleType] = mapped_column(Enum(RoleType, name="role_type_enum"), nullable=False)
    dso_id = Column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete="CASCADE"), nullable=True)
    clinic_id = Column(UUID(as_uuid=True), ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MemberInvite(Base, Autoid):
    __tablename__ = "member_invites"

    email = Column(String, nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    scope_type: Mapped[ScopeType] = mapped_column(Enum(ScopeType, name="scope_type_enum"), nullable=False)
    role: Mapped[RoleType] = mapped_column(Enum(RoleType, name="role_type_enum"), nullable=False)
    dso_id = Column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete="CASCADE"), nullable=True)
    clinic_id = Column(UUID(as_uuid=True), ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())



class AppointmentSyncLog(Base, Autoid):
    __tablename__ = "appointment_sync_logs"

    clinic_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid= True), ForeignKey("registered_clinics.id", ondelete= "CASCADE"), nullable=False)
    pat_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid= True), ForeignKey("patients.id", ondelete= "CASCADE"), nullable=True)
    appointment_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid= True), ForeignKey("appointments.id", ondelete= "SET NULL"), nullable=True)
    inbound_event_id : Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid= True), ForeignKey("inbound_events.id", ondelete= "SET NULL"), nullable=True)
    direction: Mapped[SyncDirection] = mapped_column(Enum(SyncDirection, name= "sync_direction_enum"), nullable= False)
    appointment_status: Mapped[str] = mapped_column(String, nullable=False)
    sync_status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, name="sync_status_enum"), nullable=False,default=SyncStatus.QUEUED, server_default=SyncStatus.QUEUED.name)
    change_key: Mapped[str] = mapped_column(String, nullable=False)
    event_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    apt_num: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    patient_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    patient_name_search: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True,)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer,nullable=False,default=0,server_default="0",)
    operation: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True),nullable=True)

    __table_args__ = (
        UniqueConstraint("change_key", name="uq_appointment_sync_logs_change_key"),
     
        Index(
            "ix_sync_logs_clinic_started_id",
            "clinic_id",
            "started_at",
            "id"
        ),
        Index(
            "ix_sync_logs_clinic_status_started_at",
            "clinic_id",
            "sync_status",
            "started_at",
            "id"
        ),

        Index(
            "ix_sync_logs_started_id",
            "started_at",
            "id",
        )

    )
