"""Add uploaded_by_user_id to edge_report_ingest

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-09-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "edge_report_ingest",
        sa.Column("uploaded_by_user_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("edge_report_ingest", "uploaded_by_user_id")
