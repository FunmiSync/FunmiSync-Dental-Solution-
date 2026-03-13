"""make patient pat_num nullable during reservation

Revision ID: e5d8c7b6a1f0
Revises: c91d7ab0f4b2
Create Date: 2026-03-13 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5d8c7b6a1f0"
down_revision: Union[str, Sequence[str], None] = "c91d7ab0f4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("patients", "pat_num", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("patients", "pat_num", existing_type=sa.Integer(), nullable=False)
