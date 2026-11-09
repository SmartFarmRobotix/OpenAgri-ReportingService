"""Add session_report_index

Revision ID: a1b2c3d4e5f6
Revises: 778e17c2a7ea
Create Date: 2026-08-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "778e17c2a7ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_report_index",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("report_uuid", sa.String(), nullable=False),
        sa.Column("pdf_file_name", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("images_embedded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(
        op.f("ix_session_report_index_report_uuid"),
        "session_report_index",
        ["report_uuid"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_session_report_index_report_uuid"), table_name="session_report_index")
    op.drop_table("session_report_index")
