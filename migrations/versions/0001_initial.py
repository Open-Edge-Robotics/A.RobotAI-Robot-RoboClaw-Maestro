"""Create aggregate and event tables.

Revision ID: 0001_initial
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("mission_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("mission_id"),
    )
    op.create_table(
        "robots",
        sa.Column("robot_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("robot_id"),
    )
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("aggregate_id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_events_aggregate_id", "events", ["aggregate_id"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_events_occurred_at", table_name="events")
    op.drop_index("ix_events_aggregate_id", table_name="events")
    op.drop_table("events")
    op.drop_table("robots")
    op.drop_table("missions")
