"""execution persistence

Revision ID: 0001_execution_persistence
Revises:
Create Date: 2026-09-25
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_execution_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_time", sa.Float(), nullable=False),
        sa.Column("observed_events_json", sa.Text(), nullable=False),
        sa.Column("human_selections_json", sa.Text(), nullable=False),
        sa.Column("selected_optional_ids_json", sa.Text(), nullable=False),
        sa.Column("scenario_capacities_json", sa.Text(), nullable=False),
        sa.Column("stability_weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execution_sessions_project_key", "execution_sessions", ["project_key"])
    op.create_index("ix_execution_sessions_status", "execution_sessions", ["status"])

    op.create_table(
        "discovered_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("discovered_at", sa.Float(), nullable=False),
        sa.Column("source_task_id", sa.String(length=64), nullable=True),
        sa.Column("source_event", sa.String(length=255), nullable=True),
        sa.Column("wbs", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["execution_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "task_id", name="uq_discovered_task_session_task"),
    )
    op.create_index("ix_discovered_tasks_session_id", "discovered_tasks", ["session_id"])

    op.create_table(
        "discovered_task_resources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discovered_task_id", sa.Integer(), nullable=False),
        sa.Column("mode_name", sa.String(length=128), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=False),
        sa.Column("demand", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["discovered_task_id"], ["discovered_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "discovered_task_id",
            "mode_name",
            "resource_name",
            name="uq_discovered_task_mode_resource",
        ),
    )
    op.create_index(
        "ix_discovered_task_resources_discovered_task_id",
        "discovered_task_resources",
        ["discovered_task_id"],
    )

    op.create_table(
        "discovered_task_predecessors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discovered_task_id", sa.Integer(), nullable=False),
        sa.Column("predecessor_id", sa.String(length=64), nullable=False),
        sa.Column("relation", sa.String(length=2), nullable=False),
        sa.Column("lag", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["discovered_task_id"], ["discovered_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "discovered_task_id",
            "predecessor_id",
            name="uq_discovered_task_predecessor",
        ),
    )
    op.create_index(
        "ix_discovered_task_predecessors_discovered_task_id",
        "discovered_task_predecessors",
        ["discovered_task_id"],
    )

    op.create_table(
        "discovered_task_successors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discovered_task_id", sa.Integer(), nullable=False),
        sa.Column("successor_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["discovered_task_id"], ["discovered_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "discovered_task_id",
            "successor_id",
            name="uq_discovered_task_successor",
        ),
    )
    op.create_index(
        "ix_discovered_task_successors_discovered_task_id",
        "discovered_task_successors",
        ["discovered_task_id"],
    )

    op.create_table(
        "execution_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["execution_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execution_events_session_id", "execution_events", ["session_id"])
    op.create_index("ix_execution_events_event_type", "execution_events", ["event_type"])
    op.create_index("ix_execution_events_task_id", "execution_events", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_execution_events_task_id", table_name="execution_events")
    op.drop_index("ix_execution_events_event_type", table_name="execution_events")
    op.drop_index("ix_execution_events_session_id", table_name="execution_events")
    op.drop_table("execution_events")

    op.drop_index(
        "ix_discovered_task_successors_discovered_task_id",
        table_name="discovered_task_successors",
    )
    op.drop_table("discovered_task_successors")

    op.drop_index(
        "ix_discovered_task_predecessors_discovered_task_id",
        table_name="discovered_task_predecessors",
    )
    op.drop_table("discovered_task_predecessors")

    op.drop_index(
        "ix_discovered_task_resources_discovered_task_id",
        table_name="discovered_task_resources",
    )
    op.drop_table("discovered_task_resources")

    op.drop_index("ix_discovered_tasks_session_id", table_name="discovered_tasks")
    op.drop_table("discovered_tasks")

    op.drop_index("ix_execution_sessions_status", table_name="execution_sessions")
    op.drop_index("ix_execution_sessions_project_key", table_name="execution_sessions")
    op.drop_table("execution_sessions")
