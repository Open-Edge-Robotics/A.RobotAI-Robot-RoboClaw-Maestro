"""Add persistent resource leases.

Revision ID: 0003_resource_leases
Revises: 0002_commands
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_resource_leases"
down_revision = "0002_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_leases",
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("resource_id"),
    )
    op.create_index("ix_resource_leases_owner_id", "resource_leases", ["owner_id"])
    op.create_index("ix_resource_leases_expires_at", "resource_leases", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_resource_leases_expires_at", table_name="resource_leases")
    op.drop_index("ix_resource_leases_owner_id", table_name="resource_leases")
    op.drop_table("resource_leases")
