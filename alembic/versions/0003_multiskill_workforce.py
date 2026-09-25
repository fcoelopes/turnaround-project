"""multi-skill workforce profile

Revision ID: 0003_multiskill_workforce
Revises: 0002_scope_rule_rows
Create Date: 2026-09-25
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_multiskill_workforce"
down_revision = "0002_scope_rule_rows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workforce_profiles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("workforce_profiles")
