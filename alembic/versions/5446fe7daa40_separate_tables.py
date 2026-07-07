"""separate_tables

Revision ID: 5446fe7daa40
Revises: 001_initial
Create Date: 2026-07-03 10:33:34.312338
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5446fe7daa40'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy.dialects.postgresql import ARRAY

    # ---- unofficial_medicines -----------------------------------------------
    op.create_table(
        "unofficial_medicines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generic_name", sa.Text(), nullable=False),
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
            name="uq_unofficial_medicine_form_strength",
        ),
    )
    op.create_index(
        "ix_unofficial_medicines_generic_name",
        "unofficial_medicines",
        ["generic_name"],
    )
    op.create_index(
        "ix_unofficial_medicines_notification_number",
        "unofficial_medicines",
        ["notification_number"],
    )
    op.create_index(
        "ix_unofficial_medicines_notification_date",
        "unofficial_medicines",
        ["notification_date"],
    )

    # ---- ayush_fssai_medicines ----------------------------------------------
    op.create_table(
        "ayush_fssai_medicines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("generic_name", sa.Text(), nullable=False),
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
            name="uq_ayush_fssai_medicine_form_strength",
        ),
    )
    op.create_index(
        "ix_ayush_fssai_medicines_generic_name",
        "ayush_fssai_medicines",
        ["generic_name"],
    )
    op.create_index(
        "ix_ayush_fssai_medicines_notification_number",
        "ayush_fssai_medicines",
        ["notification_number"],
    )
    op.create_index(
        "ix_ayush_fssai_medicines_notification_date",
        "ayush_fssai_medicines",
        ["notification_date"],
    )

    # ---- Data migration and cleanup -----------------------------------------
    # 1. Delete State FDA entries
    op.execute(
        "DELETE FROM banned_medicines WHERE (source_pdf LIKE '%.gov.in' OR source_pdf LIKE '%state%') "
        "AND source_pdf NOT LIKE '%cdsco%' AND source_pdf NOT LIKE '%ayush%' AND source_pdf NOT LIKE '%fssai%'"
    )

    # 2. Copy unofficial aggregator records to unofficial_medicines table
    op.execute(
        "INSERT INTO unofficial_medicines (generic_name, brand_names, dosage_form, strength, "
        "notification_number, notification_date, ban_reason, source_pdf, date_added, last_updated) "
        "SELECT generic_name, brand_names, dosage_form, strength, "
        "notification_number, notification_date, ban_reason, source_pdf, date_added, last_updated "
        "FROM banned_medicines WHERE source_pdf LIKE '%vaayath%' OR source_pdf LIKE '%thehealthmaster%' OR source_pdf LIKE '%healthmaster%'"
    )

    # 3. Delete unofficial aggregator records from banned_medicines table
    op.execute(
        "DELETE FROM banned_medicines WHERE source_pdf LIKE '%vaayath%' OR source_pdf LIKE '%thehealthmaster%' OR source_pdf LIKE '%healthmaster%'"
    )


def downgrade() -> None:
    # ---- Restore unofficial entries -----------------------------------------
    op.execute(
        "INSERT INTO banned_medicines (generic_name, brand_names, dosage_form, strength, "
        "notification_number, notification_date, ban_reason, source_pdf, date_added, last_updated) "
        "SELECT generic_name, brand_names, dosage_form, strength, "
        "notification_number, notification_date, ban_reason, source_pdf, date_added, last_updated "
        "FROM unofficial_medicines"
    )

    # ---- Drop tables and indexes --------------------------------------------
    op.drop_index("ix_ayush_fssai_medicines_notification_date", table_name="ayush_fssai_medicines")
    op.drop_index("ix_ayush_fssai_medicines_notification_number", table_name="ayush_fssai_medicines")
    op.drop_index("ix_ayush_fssai_medicines_generic_name", table_name="ayush_fssai_medicines")
    op.drop_table("ayush_fssai_medicines")

    op.drop_index("ix_unofficial_medicines_notification_date", table_name="unofficial_medicines")
    op.drop_index("ix_unofficial_medicines_notification_number", table_name="unofficial_medicines")
    op.drop_index("ix_unofficial_medicines_generic_name", table_name="unofficial_medicines")
    op.drop_table("unofficial_medicines")
