"""Add safety approval requests.

Revision ID: 0004_approvals
Revises: 0003_resource_leases
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_approvals"
down_revision = "0003_resource_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("approval_id"),
    )
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_created_at", "approvals", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_approvals_created_at", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_table("approvals")
