"""formal baseline revisions

Revision ID: 0005_baseline_revisions
Revises: 0004_approved_planning_baselines
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_baseline_revisions"
down_revision = "0004_approved_planning_baselines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "baseline_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("makespan_h", sa.Float(), nullable=False),
        sa.Column("deadline_h", sa.Float(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["execution_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "revision_number",
            name="uq_baseline_revision_session_number",
        ),
    )
    op.create_index(
        "ix_baseline_revisions_session_id",
        "baseline_revisions",
        ["session_id"],
    )
    op.create_index(
        "ix_baseline_revisions_project_key",
        "baseline_revisions",
        ["project_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_baseline_revisions_project_key",
        table_name="baseline_revisions",
    )
    op.drop_index(
        "ix_baseline_revisions_session_id",
        table_name="baseline_revisions",
    )
    op.drop_table("baseline_revisions")
