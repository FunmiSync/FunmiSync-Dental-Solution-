"""migrate ids to postgres uuid

Revision ID: c91d7ab0f4b2
Revises: 749b6eea860b
Create Date: 2026-03-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c91d7ab0f4b2"
down_revision: Union[str, Sequence[str], None] = "749b6eea860b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ID_TABLES = (
    "users",
    '"Dsos"',
    "registered_clinics",
    "patients",
    "appointments",
    "audit_logs",
    "role_assignments",
    "member_invites",
)


def _drop_fk(table: str, constraint: str) -> None:
    op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}')


def _alter_uuid(table: str, column: str, *, nullable: bool = False) -> None:
    if nullable:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE uuid USING NULLIF({column}, \'\')::uuid'
        )
    else:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE uuid USING {column}::uuid'
        )


def _alter_text(table: str, column: str) -> None:
    op.execute(
        f'ALTER TABLE {table} ALTER COLUMN {column} TYPE varchar USING {column}::text'
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    _drop_fk('"Dsos"', '"Dsos_user_id_fkey"')
    _drop_fk("registered_clinics", "registered_clinics_owner_id_fkey")
    _drop_fk("registered_clinics", "registered_clinics_dso_id_fkey")
    _drop_fk("patients", "patients_clinic_id_fkey")
    _drop_fk("appointments", "appointments_clinic_id_fkey")
    _drop_fk("appointments", "appointments_pat_id_fkey")
    _drop_fk("role_assignments", "role_assignments_user_id_fkey")
    _drop_fk("role_assignments", "role_assignments_dso_id_fkey")
    _drop_fk("role_assignments", "role_assignments_clinic_id_fkey")
    _drop_fk("role_assignments", "role_assignments_created_by_fkey")
    _drop_fk("member_invites", "member_invites_dso_id_fkey")
    _drop_fk("member_invites", "member_invites_clinic_id_fkey")
    _drop_fk("member_invites", "member_invites_created_by_fkey")

    _alter_uuid("users", "id")
    _alter_uuid('"Dsos"', "id")
    _alter_uuid("registered_clinics", "id")
    _alter_uuid("patients", "id")
    _alter_uuid("appointments", "id")
    _alter_uuid("audit_logs", "id")
    _alter_uuid("role_assignments", "id")
    _alter_uuid("member_invites", "id")

    _alter_uuid('"Dsos"', "user_id")
    _alter_uuid("registered_clinics", "owner_id")
    _alter_uuid("registered_clinics", "dso_id", nullable=True)
    _alter_uuid("patients", "clinic_id")
    _alter_uuid("appointments", "clinic_id")
    _alter_uuid("appointments", "pat_id")
    _alter_uuid("audit_logs", "clinic_id")
    _alter_uuid("role_assignments", "user_id")
    _alter_uuid("role_assignments", "dso_id", nullable=True)
    _alter_uuid("role_assignments", "clinic_id", nullable=True)
    _alter_uuid("role_assignments", "created_by", nullable=True)
    _alter_uuid("member_invites", "dso_id", nullable=True)
    _alter_uuid("member_invites", "clinic_id", nullable=True)
    _alter_uuid("member_invites", "created_by", nullable=True)

    for table in ID_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id SET DEFAULT gen_random_uuid()")

    op.create_foreign_key("Dsos_user_id_fkey", "Dsos", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("registered_clinics_owner_id_fkey", "registered_clinics", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("registered_clinics_dso_id_fkey", "registered_clinics", "Dsos", ["dso_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("patients_clinic_id_fkey", "patients", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("appointments_clinic_id_fkey", "appointments", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("appointments_pat_id_fkey", "appointments", "patients", ["pat_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_user_id_fkey", "role_assignments", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_dso_id_fkey", "role_assignments", "Dsos", ["dso_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_clinic_id_fkey", "role_assignments", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_created_by_fkey", "role_assignments", "users", ["created_by"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("member_invites_dso_id_fkey", "member_invites", "Dsos", ["dso_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("member_invites_clinic_id_fkey", "member_invites", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("member_invites_created_by_fkey", "member_invites", "users", ["created_by"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    _drop_fk('"Dsos"', '"Dsos_user_id_fkey"')
    _drop_fk("registered_clinics", "registered_clinics_owner_id_fkey")
    _drop_fk("registered_clinics", "registered_clinics_dso_id_fkey")
    _drop_fk("patients", "patients_clinic_id_fkey")
    _drop_fk("appointments", "appointments_clinic_id_fkey")
    _drop_fk("appointments", "appointments_pat_id_fkey")
    _drop_fk("role_assignments", "role_assignments_user_id_fkey")
    _drop_fk("role_assignments", "role_assignments_dso_id_fkey")
    _drop_fk("role_assignments", "role_assignments_clinic_id_fkey")
    _drop_fk("role_assignments", "role_assignments_created_by_fkey")
    _drop_fk("member_invites", "member_invites_dso_id_fkey")
    _drop_fk("member_invites", "member_invites_clinic_id_fkey")
    _drop_fk("member_invites", "member_invites_created_by_fkey")

    for table in ID_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT")

    _alter_text("member_invites", "created_by")
    _alter_text("member_invites", "clinic_id")
    _alter_text("member_invites", "dso_id")
    _alter_text("role_assignments", "created_by")
    _alter_text("role_assignments", "clinic_id")
    _alter_text("role_assignments", "dso_id")
    _alter_text("role_assignments", "user_id")
    _alter_text("audit_logs", "clinic_id")
    _alter_text("appointments", "pat_id")
    _alter_text("appointments", "clinic_id")
    _alter_text("patients", "clinic_id")
    _alter_text("registered_clinics", "dso_id")
    _alter_text("registered_clinics", "owner_id")
    _alter_text('"Dsos"', "user_id")

    _alter_text("member_invites", "id")
    _alter_text("role_assignments", "id")
    _alter_text("audit_logs", "id")
    _alter_text("appointments", "id")
    _alter_text("patients", "id")
    _alter_text("registered_clinics", "id")
    _alter_text('"Dsos"', "id")
    _alter_text("users", "id")

    op.create_foreign_key("Dsos_user_id_fkey", "Dsos", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("registered_clinics_owner_id_fkey", "registered_clinics", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("registered_clinics_dso_id_fkey", "registered_clinics", "Dsos", ["dso_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("patients_clinic_id_fkey", "patients", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("appointments_clinic_id_fkey", "appointments", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("appointments_pat_id_fkey", "appointments", "patients", ["pat_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_user_id_fkey", "role_assignments", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_dso_id_fkey", "role_assignments", "Dsos", ["dso_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_clinic_id_fkey", "role_assignments", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("role_assignments_created_by_fkey", "role_assignments", "users", ["created_by"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("member_invites_dso_id_fkey", "member_invites", "Dsos", ["dso_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("member_invites_clinic_id_fkey", "member_invites", "registered_clinics", ["clinic_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("member_invites_created_by_fkey", "member_invites", "users", ["created_by"], ["id"], ondelete="SET NULL")
