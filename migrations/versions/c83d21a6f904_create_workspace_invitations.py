"""Create workspace invitations.

Revision ID: c83d21a6f904
Revises: 7fe37278ed55
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c83d21a6f904"
down_revision: str | Sequence[str] | None = "7fe37278ed55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This enum belongs to the preceding membership migration. Do not recreate it.
    role_type = postgresql.ENUM(
        "owner", "marketer", "analyst", name="membership_role", create_type=False
    )
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("normalized_email", sa.String(320), nullable=False),
        sa.Column("role", role_type, nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["accepted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_hash", name="uq_workspace_invitations_token_hash"),
        sa.CheckConstraint(
            "role IN ('marketer', 'analyst')", name="ck_workspace_invitations_allowed_role"
        ),
        sa.CheckConstraint(
            "length(trim(normalized_email)) > 0 AND normalized_email = trim(normalized_email)",
            name="ck_workspace_invitations_normalized_email",
        ),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_workspace_invitations_hash_length"),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_workspace_invitations_expiry_after_creation"
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= created_at AND accepted_at < expires_at)",
            name="ck_workspace_invitations_acceptance_window",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_workspace_invitations_revocation_after_creation",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name="ck_workspace_invitations_not_accepted_and_revoked",
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL AND accepted_by_user_id IS NULL) OR "
            "(accepted_at IS NOT NULL AND accepted_by_user_id IS NOT NULL)",
            name="ck_workspace_invitations_acceptance_actor",
        ),
    )
    op.create_index(
        "uq_workspace_invitations_open_email",
        "workspace_invitations",
        ["workspace_id", "normalized_email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
        sqlite_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "ix_workspace_invitations_inviter_lifecycle",
        "workspace_invitations",
        ["invited_by_user_id", "accepted_at", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_invitations_inviter_lifecycle", table_name="workspace_invitations")
    op.drop_index("uq_workspace_invitations_open_email", table_name="workspace_invitations")
    op.drop_table("workspace_invitations")
    # membership_role is shared with workspace_memberships and must remain.
