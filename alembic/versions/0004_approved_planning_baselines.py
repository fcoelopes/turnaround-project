"""approved planning baselines

Revision ID: 0004_approved_planning_baselines
Revises: 0003_multiskill_workforce
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_approved_planning_baselines"
down_revision = "0003_multiskill_workforce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planning_baselines",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("makespan_h", sa.Float(), nullable=False),
        sa.Column("deadline_h", sa.Float(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_planning_baselines_updated_at",
        "planning_baselines",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_planning_baselines_updated_at",
        table_name="planning_baselines",
    )
    op.drop_table("planning_baselines")
