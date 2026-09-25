"""spreadsheet scope rules

Revision ID: 0002_scope_rule_rows
Revises: 0001_execution_persistence
Create Date: 2026-09-25
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_scope_rule_rows"
down_revision = "0001_execution_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scope_rule_rows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_key",
            "rule_id",
            name="uq_scope_rule_project_rule",
        ),
    )
    op.create_index(
        "ix_scope_rule_rows_project_key",
        "scope_rule_rows",
        ["project_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scope_rule_rows_project_key",
        table_name="scope_rule_rows",
    )
    op.drop_table("scope_rule_rows")
