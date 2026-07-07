"""Initial schema — banned_medicines and source_documents tables.

Revision ID: 001_initial
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- banned_medicines ---------------------------------------------------
    op.create_table(
        "banned_medicines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generic_name", sa.String(length=255), nullable=False),
        sa.Column("brand_names", ARRAY(sa.Text()), nullable=True),
        sa.Column("dosage_form", sa.String(length=100), nullable=True),
        sa.Column("strength", sa.String(length=50), nullable=True),
        sa.Column("notification_number", sa.String(length=50), nullable=True),
        sa.Column("notification_date", sa.Date(), nullable=True),
        sa.Column("ban_reason", sa.Text(), nullable=True),
        sa.Column("source_pdf", sa.String(length=255), nullable=True),
        sa.Column(
            "date_added",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.Column(
            "last_updated",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generic_name",
            "dosage_form",
            "strength",
            name="uq_medicine_form_strength",
        ),
    )
    op.create_index(
        "ix_banned_medicines_generic_name",
        "banned_medicines",
        ["generic_name"],
    )
    op.create_index(
        "ix_banned_medicines_notification_number",
        "banned_medicines",
        ["notification_number"],
    )
    op.create_index(
        "ix_banned_medicines_notification_date",
        "banned_medicines",
        ["notification_date"],
    )

    # ---- source_documents ---------------------------------------------------
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column(
            "download_date", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("notification_number", sa.String(length=50), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(length=20),
            server_default="pending",
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_name"),
    )


def downgrade() -> None:
    op.drop_table("source_documents")
    op.drop_index("ix_banned_medicines_notification_date", table_name="banned_medicines")
    op.drop_index("ix_banned_medicines_notification_number", table_name="banned_medicines")
    op.drop_index("ix_banned_medicines_generic_name", table_name="banned_medicines")
    op.drop_table("banned_medicines")
