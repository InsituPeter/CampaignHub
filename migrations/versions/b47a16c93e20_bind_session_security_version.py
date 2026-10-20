"""Bind refresh sessions to a security version; invalidate legacy sessions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b47a16c93e20"
down_revision: str | Sequence[str] | None = "9baa1d188469"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("security_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_check_constraint(
        "ck_user_sessions_security_version_positive", "user_sessions", "security_version >= 1"
    )
    # Legacy sessions have no reliable issuance-version snapshot. Fail closed.
    op.execute(
        "UPDATE user_sessions SET revoked_at = GREATEST(CURRENT_TIMESTAMP, issued_at), "
        "revocation_reason = 'session_security_upgrade' WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_sessions_security_version_positive", "user_sessions", type_="check")
    op.drop_column("user_sessions", "security_version")
    # Revocation is intentionally not undone by a schema downgrade.
