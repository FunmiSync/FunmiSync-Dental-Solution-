from sqlalchemy import Column, String, Integer, DateTime, Text, func, ForeignKey, UniqueConstraint, Date, BigInteger, Boolean, Enum, text
from sqlalchemy.orm import relationship, Mapped, mapped_column
from datetime import datetime, date
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


class SyncFailureSource(str, enum.Enum):
    NONE = "none"
    CUSTOMER_CONFIGURATION = "customer_configuration"
    OPEN_DENTAL = "open_dental"
    CRM = "crm"
    WEBHOOK = "webhook"
    INTERNAL = "internal"
    UNKNOWN = "unknown"



class WalletType(str, enum.Enum):
    DSO_TREASURY = "dso_treasury"
    CLINIC = "clinic"



class WalletStatus(str, enum.Enum):
    PENDING ="pending"
    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED ="failed"



class WalletAuthorizationStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    AUTHORIZED = "authorized"
    REVOKED = "revoked"
    FAILED = "failed"


class PaymentProvider(str, enum.Enum):
    TOROFORGE =  "toroforge"
    STRIPE ="stripe"
    INTERNAL = "internal"


class SubscriptionStatus(str, enum.Enum):
    TRAILING ="trailing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"
    CANCELLED = "cancelled"


class BillingCycle(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"



class BillingPlanCode(str, enum.Enum):
    STARTER = "starter"
    BRONZE = "bronze"
    PREMIUM = "premium"



class UsageFeatureCode(str, enum.Enum):
    MESSAGING = "messaging"
    ELIGIBILITY = "eligibility"
    TICKET_AUTOMATION = "ticket_automation"
    SYNC_OVERAGE = "sync_overage"


class UsageBillingStatus(str, enum.Enum):
    PENDING = "pending"
    CHARGED = "charged"
    FAILED = "failed"
    REFUNDED = "refunded"
    WAIVED ="waived"


class DailySyncAggregationStatus(str, enum.Enum):
    PENDING ="pending"
    CHARGED = "charged"
    FAILED = "failed"
    NO_OVERAGE = "no_overage"



class LedgerEntryType(str, enum.Enum):
    TOP_UP = "top_up"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    SUBSCRIPTION_CHARGE = "subscription_charge"
    USAGE_CHARGE = "usage_charge"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class LedgerDirection(str, enum.Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class LedgerStatus(str, enum.Enum):
    PENDING = "pending"
    POSTED = "posted"
    FAILED = "failed"
    REVERSED = "reversed"


class WalletTransferStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class PaymentTransactionStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"





class Users(Base, Autoid):
    __tablename__ = "users"
    username = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password = Column(String, nullable=False)
    google_sub: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)
    auth_provider: Mapped[str] = mapped_column(String, nullable=False, default="local", server_default="local")
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    refresh_jti: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
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
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    dso_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete="CASCADE"), nullable=True, index=True)
    last_webhook_auth_failed_at: Mapped[Optional[datetime]] = mapped_column(
    DateTime(timezone=True),nullable=True)
    webhook_auth_failure_count: Mapped[int] = mapped_column(Integer, nullable=False,default=0, server_default="0")
    od_health_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    od_health_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    od_health_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    crm_health_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    crm_health_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    crm_health_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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

    __table_args__ = (
    Index(
        "ix_inbound_events_clinic_received_at",
        "clinic_id",
        "received_at",
    ),
)



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

    __table_args__ = (
        Index(
            "ix_role_assignments_user_scope_dso_active",
            "user_id",
            "scope_type",
            "dso_id",
            "is_active",
        ),
        Index(
            "ix_role_assignments_user_scope_clinic_active",
            "user_id",
            "scope_type",
            "clinic_id",
            "is_active",
        ),
    )


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
    failure_source: Mapped[SyncFailureSource] = mapped_column(Enum(SyncFailureSource, name="sync_failure_source_enum"), nullable=False, default=SyncFailureSource.NONE,server_default=SyncFailureSource.NONE.value,index=True)
    counts_toward_usage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,server_default="false", index=True)
    is_billable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,server_default="false", index=True)
    billing_processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True),nullable=True)
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



class Wallet(Base, Autoid):
    __tablename__ = "wallets"

    dso_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete= "CASCADE"), nullable = True,  index= True)

    clinic_id: Mapped[Optional[uuid.UUID]] =  mapped_column(UUID(as_uuid= True), ForeignKey("registered_clnic.id", ondelete= "CASCADE"), nullable= True, index =True)

    parent_wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid= True), ForeignKey("wallets.id", ondelete= "SET NULL"), nullable= True, index = True)

    wallet_type: Mapped[WalletType] = mapped_column(Enum(WalletType, name="wallet_type_enum"), nullable= False, index= True)

    status: Mapped[WalletStatus] =mapped_column(Enum(WalletStatus, name= "wallet_status_enum"), nullable = False , default= WalletStatus.PENDING, server_default= WalletStatus.PENDING.name, index= True)

    external_wallet_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index= True)

    external_wallet_address: Mapped[Optional[str]] = mapped_column(String, nullable= True, index= True) 

    currency: Mapped[str] = mapped_column(String, nullable=False, default= "USD", server_default="USD")

    cached_balance_minor: Mapped[int] = mapped_column(BigInteger, nullable= False, default= 0, server_default= "0")

    auto_debit_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default= False, server_default= "false", index= True)

    last_balance_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable= True)

    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable= True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone= True), nullable= False, server_default=func.now())

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone= True), nullable= False, server_default=func.now(), onupdate=func.now())


    __table_args__ = (
        UniqueConstraint("dso_id", "wallet_type", name = "up_wallet_dso_type"),
        UniqueConstraint("clinic_id", "wallet_type", name= "uq_wallet_clinic_type"),
        Index("ix_walletss_dso_clinic_parent", "dso_id", "clinic_id", "parent_wallet_id")
    )



class BillingSubscription(Base, Autoid):
    __tablename__ = "billing_subscriptions"

    scope_type: Mapped[ScopeType] = mapped_column(Enum(ScopeType, name="scope_type_enum"), nullable= False, Index =True)

    dso_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("Dso.id", ondelete= "CASCADE"), nullable= True, index = True)

    clinic_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid= True), ForeignKey("registered_clinics.id", ondelete= "CASCADE"), nullable= True, index= True )

    wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid = True), ForeignKey("wallets.id", ondelete= "SET NULL"), nullable= True, index= True)

    status: Mapped[SubscriptionStatus] = mapped_column(UUID(as_uuid= True), Enum(SubscriptionStatus, name= "subscription_status_enum"), nullable=False, default=SubscriptionStatus.TRAILING, server_default= SubscriptionStatus.TRAILING, index= True)

    billing_cycle: Mapped[BillingCycle] = mapped_column(Enum(BillingCycle, name= "Billing_cycle_enum"), nullable= False, default= BillingCycle.MONTHLY, server_deafult = BillingCycle.MONTHLY.name)

    payment_provider:  Mapped[PaymentProvider] = mapped_column(Enum(PaymentProvider, name= "payment_provider_enum"), nullable=False, default=PaymentProvider.STRIPE, server_default=PaymentProvider.STRIPE.name, index= True)

    plan_code: Mapped[BillingPlanCode] = mapped_column(Enum(BillingPlanCode,name="billing_plan_code_enum"),
    nullable=False, index=True)

    base_price_minor: Mapped[int] = mapped_column(BigInteger, nullable= False)

    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD", server_default="USD")

    included_sync_threshold: Mapped[int] = mapped_column(Integer, nullable=False)

    external_subscription_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    next_billing_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_billing_subscriptions_scope_status", "scope_type", "status"),
        Index("ix_billing_subscriptions_dso_status", "dso_id", "status"),
        Index("ix_billing_subscriptions_clinic_status", "clinic_id", "status"),
    )




class UsageEvent(Base, Autoid):
    __tablename__ = "usage_events"

    wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="SET NULL"), nullable=True,index=True)

    dso_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete="SET NULL"), nullable=True, index=True)

    clinic_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("registered_clinics.id", ondelete="SET NULL"),  nullable=True, index=True)

    feature_code: Mapped[UsageFeatureCode] = mapped_column(Enum(UsageFeatureCode, name="usage_feature_code_enum"), nullable=False, index=True,
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD", server_default="USD")

    billing_status: Mapped[UsageBillingStatus] = mapped_column(Enum(UsageBillingStatus, name="usage_billing_status_enum"), nullable=False, default=UsageBillingStatus.PENDING,server_default=UsageBillingStatus.PENDING.name, index=True)

    charge_provider: Mapped[Optional[PaymentProvider]] = mapped_column(Enum(PaymentProvider, name="usage_charge_provider_enum"), nullable=True, index=True)

    reference_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    reference_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_usage_events_clinic_feature_created", "clinic_id", "feature_code", "created_at"),
        Index("ix_usage_events_dso_feature_created", "dso_id", "feature_code", "created_at"),
    )




class DailySyncUsageSummary(Base, Autoid):
    __tablename__ = "daily_sync_usage_summaries"

    dso_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete="SET NULL"), nullable=True,index=True)

    clinic_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable=False, index=True)

    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    total_sync_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    successful_syncs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    customer_configuration_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    open_dental_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    crm_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    webhook_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    internal_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    unknown_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    counted_usage_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    included_units_applied: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    billable_overage_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    aggregation_status: Mapped[DailySyncAggregationStatus] = mapped_column(Enum(DailySyncAggregationStatus, name="daily_sync_aggregation_status_enum"), nullable=False,default=DailySyncAggregationStatus.PENDING, server_default=DailySyncAggregationStatus.PENDING.name,index=True,)

    usage_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usage_events.id", ondelete="SET NULL"), nullable=True,index=True)

    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,server_default=func.now())

    __table_args__ = (
        UniqueConstraint("clinic_id", "usage_date", name="uq_daily_sync_usage_clinic_date"),
    )
    



class WalletLedgerEntry(Base, Autoid):
    __tablename__ = "wallet_ledger_entries"

    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True)

    counterparty_wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True),ForeignKey("wallets.id", ondelete="SET NULL"), nullable=True,index=True)

    transaction_group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False,default=uuid.uuid4, index=True)

    entry_type: Mapped[LedgerEntryType] = mapped_column(Enum(LedgerEntryType, name="ledger_entry_type_enum"), nullable=False, index=True)

    direction: Mapped[LedgerDirection] = mapped_column(Enum(LedgerDirection, name="ledger_direction_enum"), nullable=False,index=True)

    status: Mapped[LedgerStatus] = mapped_column(Enum(LedgerStatus, name="ledger_status_enum"),nullable=False, default=LedgerStatus.PENDING, server_default=LedgerStatus.PENDING.name, index=True)

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD", server_default="USD")

    balance_after_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    provider: Mapped[PaymentProvider] = mapped_column(Enum(PaymentProvider, name="ledger_payment_provider_enum"), nullable=False, default=PaymentProvider.INTERNAL,server_default=PaymentProvider.INTERNAL.name, index=True)

    external_transaction_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    reference_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    reference_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_wallet_ledger_wallet_created", "wallet_id", "created_at"),
        Index("ix_wallet_ledger_reference", "reference_type", "reference_id"),
    )



class WalletTransfer(Base, Autoid):
    __tablename__ = "wallet_transfers"

    from_wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False,index=True)

    to_wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True)

    initiated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD", server_default="USD")

    status: Mapped[WalletTransferStatus] = mapped_column(Enum(WalletTransferStatus, name="wallet_transfer_status_enum"), nullable=False, default=WalletTransferStatus.PENDING, server_default=WalletTransferStatus.PENDING.name, index=True)

    external_transaction_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())




class PaymentTransaction(Base, Autoid):
    __tablename__ = "payment_transactions"

    dso_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("Dsos.id", ondelete="SET NULL"), nullable=True,index=True)

    clinic_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("registered_clinics.id", ondelete="SET NULL"), nullable=True, index=True)

    wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="SET NULL"), nullable=True, index=True)

    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("billing_subscriptions.id", ondelete="SET NULL"), nullable=True, index=True,)

    provider: Mapped[PaymentProvider] = mapped_column(
        Enum(PaymentProvider, name="payment_transaction_provider_enum"),
        nullable=False,
        index=True,
    )

    purpose: Mapped[str] = mapped_column(String, nullable=False, index=True)

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD", server_default="USD")

    external_payment_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)

    status: Mapped[PaymentTransactionStatus] = mapped_column(
        Enum(PaymentTransactionStatus, name="payment_transaction_status_enum"),
        nullable=False,
        default=PaymentTransactionStatus.PENDING,
        server_default=PaymentTransactionStatus.PENDING.name,
        index=True,
    )

    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    succeeded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_payment_transactions_dso_created", "dso_id", "created_at"),
        Index("ix_payment_transactions_clinic_created", "clinic_id", "created_at"),
        Index("ix_payment_transactions_wallet_created", "wallet_id", "created_at"),
    )