"""Per-clinic line of CRM events: ordering, retries, OpenDental pauses, notes and alerts.

Revision ID: f4a100000002
Revises: f4a100000001

Adds nullable columns, two new tables and indexes; nothing is rewritten or removed, so
tables are not locked for long. Events already waiting keep working: the worker finds
their sync log through appointment_sync_logs.inbound_event_id when
inbound_events.sync_log_id is empty.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f4a100000002"
down_revision = "f4a100000001"
branch_labels = None
depends_on = None

WAITING = "processing_status IN ('queued', 'waiting', 'retrying', 'needs_attention', 'processing')"


def upgrade():
    op.add_column("appointments", sa.Column("last_event_received_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appointments", sa.Column("note_hash", sa.String(), nullable=True))
    op.add_column("registered_clinics", sa.Column("sync_paused_until", sa.DateTime(timezone=True), nullable=True))

    op.create_table("sync_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("clinic_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("subject_key", sa.String(), nullable=False, server_default=""),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_error", sa.String(), nullable=True),
    )
    op.create_index("uq_sync_alerts_open", "sync_alerts", ["clinic_id", "code", "subject_key"], unique=True,
                    postgresql_where=sa.text("resolved_at IS NULL"))
    op.create_table("user_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sync_alerts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "alert_id", name="uq_user_notification_alert"),
    )
    op.create_index("ix_user_notifications_user_created", "user_notifications", ["user_id", "created_at"])

    op.add_column("inbound_events", sa.Column("sync_log_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("inbound_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inbound_events", sa.Column("alert_id", postgresql.UUID(as_uuid=True),
                                              sa.ForeignKey("sync_alerts.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_inbound_events_waiting", "inbound_events", ["clinic_id", "received_at"],
                    postgresql_where=sa.text(WAITING))


def downgrade():
    # These only steer the worker and its alerts; the previous code ignores them. Undoing
    # discards alert history and notifications.
    op.drop_index("ix_inbound_events_waiting", table_name="inbound_events")
    op.drop_column("inbound_events", "alert_id")
    op.drop_column("inbound_events", "next_attempt_at")
    op.drop_column("inbound_events", "sync_log_id")
    op.drop_index("ix_user_notifications_user_created", table_name="user_notifications")
    op.drop_table("user_notifications")
    op.drop_index("uq_sync_alerts_open", table_name="sync_alerts")
    op.drop_table("sync_alerts")
    op.drop_column("registered_clinics", "sync_paused_until")
    op.drop_column("appointments", "note_hash")
    op.drop_column("appointments", "last_event_received_at")
