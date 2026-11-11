"""Durable external-operation journal and treasury-only DSO uniqueness.

Revision ID: f4a100000001
Revises: d64762c7874a

Prepared only. Review the Phase 4 rollout document before executing.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f4a100000001"
down_revision = "d64762c7874a"
branch_labels = None
depends_on = None


def upgrade():
    # No destructive row repair: an invalid existing treasury must be investigated.
    invalid = op.get_bind().execute(sa.text("""
        SELECT count(*) FROM wallets
        WHERE wallet_type = 'DSO_TREASURY' AND (dso_id IS NULL OR clinic_id IS NOT NULL)
    """)).scalar_one()
    if invalid:
        raise RuntimeError("Invalid treasury ownership rows require manual reconciliation before upgrade")
    op.drop_constraint("up_wallet_dso_type", "wallets", type_="unique")
    op.create_index("uq_wallet_dso_treasury", "wallets", ["dso_id"], unique=True,
                    postgresql_where=sa.text("wallet_type = 'DSO_TREASURY'"))
    op.create_table("external_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_key", sa.String(), nullable=False, unique=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("request_encrypted", sa.Text(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("secrets_encrypted", sa.Text(), nullable=True),
        sa.Column("result_encrypted", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_external_operations_id", "external_operations", ["id"], unique=True)
    op.create_index("ix_external_operations_resource_id", "external_operations", ["resource_id"])
    op.create_index("uq_external_operation_active_resource", "external_operations", ["resource_id", "action"],
                    unique=True, postgresql_where=sa.text("state IN ('in_flight', 'uncertain', 'succeeded')"))
    op.alter_column("audit_logs", "clinic_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    for column, scope in (("dso_id", "DSO"), ("clinic_id", "CLINIC")):
        duplicates = op.get_bind().execute(sa.text(f"""
            SELECT count(*) FROM (
                SELECT user_id, {column} FROM role_assignments
                WHERE scope_type = :scope AND is_active
                GROUP BY user_id, {column} HAVING count(*) > 1
            ) duplicate_memberships
        """), {"scope":scope}).scalar_one()
        if duplicates:
            raise RuntimeError("Duplicate active memberships need manual role review before upgrade")
    op.create_index("uq_active_dso_membership", "role_assignments", ["user_id", "dso_id"], unique=True,
                    postgresql_where=sa.text("scope_type = 'DSO' AND is_active"))
    op.create_index("uq_active_clinic_membership", "role_assignments", ["user_id", "clinic_id"], unique=True,
                    postgresql_where=sa.text("scope_type = 'CLINIC' AND is_active"))
    # Restore only missing owner memberships, without changing existing roles or
    # granting DSO-wide staff access to clinic workspaces.
    op.execute("""INSERT INTO role_assignments (id,user_id,scope_type,role,dso_id,is_active,created_by)
        SELECT gen_random_uuid(),d.user_id,'DSO','ADMIN',d.id,true,d.user_id FROM "Dsos" d
        WHERE NOT EXISTS (SELECT 1 FROM role_assignments r WHERE r.user_id=d.user_id
            AND r.dso_id=d.id AND r.scope_type='DSO' AND r.is_active)""")
    op.execute("""INSERT INTO role_assignments (id,user_id,scope_type,role,clinic_id,is_active,created_by)
        SELECT gen_random_uuid(),c.owner_id,'CLINIC','ADMIN',c.id,true,c.owner_id FROM registered_clinics c
        WHERE c.dso_id IS NULL AND NOT EXISTS (SELECT 1 FROM role_assignments r WHERE r.user_id=c.owner_id
            AND r.clinic_id=c.id AND r.scope_type='CLINIC' AND r.is_active)""")


def downgrade():
    # Dropping the journal would discard the evidence needed to avoid duplicate
    # payments. Never automatically destroy it during a rollback.
    raise RuntimeError("Journal downgrade is intentionally blocked; archive/reconcile operations and review a manual rollback")
