"""Add command journal with idempotency uniqueness.

Revision ID: 0002_commands
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_commands"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commands",
        sa.Column("command_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("command_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_commands_idempotency_key", "commands", ["idempotency_key"])


def downgrade() -> None:
    op.drop_index("ix_commands_idempotency_key", table_name="commands")
    op.drop_table("commands")
