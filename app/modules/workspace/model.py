from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.models import TimestampMixin, UUIDPrimaryKeyMixin


class WorkspaceStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SENDING_SUSPENDED = "sending_suspended"
    CLOSED = "closed"


class MembershipRole(StrEnum):
    OWNER = "owner"
    MARKETER = "marketer"
    ANALYST = "analyst"


class MembershipStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    __table_args__ = (
        UniqueConstraint("slug", name="uq_workspaces_slug"),
        CheckConstraint("length(trim(name))>0", name="ck_workspaces_name_not_blank"),
        CheckConstraint(
            "slug IS NULL OR (length(trim(slug)) > 0 AND slug = lower(trim(slug)))",
            name="ck_workspaces_slug_normalized",
        ),
        CheckConstraint("default_currency = 'NGN'", name="ck_workspaces_pilot_currency"),
        CheckConstraint("version >= 1", name="ck_workspaces_version_positive"),
        Index("ix_workspaces_status", "status"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[WorkspaceStatus] = mapped_column(
        SqlEnum(
            WorkspaceStatus,
            name="workspace_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=WorkspaceStatus.PENDING,
        server_default=WorkspaceStatus.PENDING.value,
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Africa/Lagos", server_default="Africa/Lagos"
    )
    default_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NGN", server_default="NGN"
    )
    # Assign a new dictionary on updates; plain JSON does not track nested mutations.
    business_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    sending_suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    __mapper_args__ = {"version_id_col": version}


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
        CheckConstraint("version >= 1", name="ck_workspace_memberships_version_positive"),
        CheckConstraint(
            "status != 'active' OR accepted_at IS NOT NULL",
            name="ck_workspace_memberships_active_requires_acceptance",
        ),
        CheckConstraint(
            "(status = 'removed' AND removed_at IS NOT NULL) OR "
            "(status != 'removed' AND removed_at IS NULL)",
            name="ck_workspace_memberships_removal_state",
        ),
        Index("ix_workspace_memberships_user_status", "user_id", "status"),
        Index("ix_workspace_memberships_workspace_role_status", "workspace_id", "role", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # User deletion must not silently remove an owner's membership.
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        SqlEnum(
            MembershipRole,
            name="membership_role",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        SqlEnum(
            MembershipStatus,
            name="membership_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=MembershipStatus.INVITED,
        server_default=MembershipStatus.INVITED.value,
    )
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    __mapper_args__ = {"version_id_col": version}


class WorkspaceInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Invitation persistence; authorization and single-use consumption live in services.

    Issuing services set expires_at to creation time plus 48 hours and normalize
    email using the identity normalization rules. Losing owner access must revoke
    outstanding invitations permanently, not merely rely on a live role check.
    """

    __tablename__ = "workspace_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_workspace_invitations_token_hash"),
        CheckConstraint(
            "role IN ('marketer', 'analyst')", name="ck_workspace_invitations_allowed_role"
        ),
        CheckConstraint(
            "length(trim(normalized_email)) > 0 AND normalized_email = trim(normalized_email)",
            name="ck_workspace_invitations_normalized_email",
        ),
        CheckConstraint("length(token_hash) = 64", name="ck_workspace_invitations_hash_length"),
        CheckConstraint(
            "expires_at > created_at", name="ck_workspace_invitations_expiry_after_creation"
        ),
        CheckConstraint(
            "accepted_at IS NULL OR (accepted_at >= created_at AND accepted_at < expires_at)",
            name="ck_workspace_invitations_acceptance_window",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_workspace_invitations_revocation_after_creation",
        ),
        CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name="ck_workspace_invitations_not_accepted_and_revoked",
        ),
        CheckConstraint(
            "(accepted_at IS NULL AND accepted_by_user_id IS NULL) OR "
            "(accepted_at IS NOT NULL AND accepted_by_user_id IS NOT NULL)",
            name="ck_workspace_invitations_acceptance_actor",
        ),
        # No clock-dependent predicate: revoke expired open rows before replacement.
        Index(
            "uq_workspace_invitations_open_email",
            "workspace_id",
            "normalized_email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        Index(
            "ix_workspace_invitations_inviter_lifecycle",
            "invited_by_user_id",
            "accepted_at",
            "revoked_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # No invitee FK: an invitation may precede registration.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        SqlEnum(
            MembershipRole,
            name="membership_role",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )
    invited_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
