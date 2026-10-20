from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.models import TimestampMixin, UUIDPrimaryKeyMixin


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class UserActionTokenPurpose(StrEnum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


class User(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "normalized_email",
            name="uq_users_normalized_email",
        ),
        CheckConstraint(
            "security_version >= 1",
            name="ck_users_security_version_positive",
        ),
        Index(
            "ix_users_status",
            "status",
        ),
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    normalized_email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    status: Mapped[UserStatus] = mapped_column(
        SqlEnum(
            UserStatus,
            name="user_status",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
        default=UserStatus.PENDING,
        server_default=UserStatus.PENDING.value,
    )

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    security_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class UserSession(
    UUIDPrimaryKeyMixin,
    Base,
):
    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint("security_version >= 1", name="ck_user_sessions_security_version_positive"),
        UniqueConstraint(
            "refresh_token_hash",
            name="uq_user_sessions_refresh_token_hash",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_user_sessions_expiry_after_issue",
        ),
        CheckConstraint(
            "rotated_at IS NULL OR rotated_at >= issued_at",
            name="ck_user_sessions_rotation_after_issue",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_user_sessions_revocation_after_issue",
        ),
        Index(
            "ix_user_sessions_user_lifecycle",
            "user_id",
            "revoked_at",
            "expires_at",
        ),
        Index(
            "ix_user_sessions_token_family_id",
            "token_family_id",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    token_family_id: Mapped[UUID] = mapped_column(
        nullable=False,
        default=uuid4,
    )

    security_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    refresh_token_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revocation_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ip_metadata: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )


class UserActionToken(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "user_action_tokens"
    __table_args__ = (
        UniqueConstraint(
            "token_hash",
            name="uq_user_action_tokens_token_hash",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_user_action_tokens_expiry_after_creation",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="ck_user_action_tokens_consumption_after_creation",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_user_action_tokens_revocation_after_creation",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR revoked_at IS NULL",
            name="ck_user_action_tokens_not_consumed_and_revoked",
        ),
        Index(
            "uq_user_action_tokens_active_purpose",
            "user_id",
            "purpose",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index(
            "ix_user_action_tokens_user_lifecycle",
            "user_id",
            "purpose",
            "expires_at",
            "consumed_at",
            "revoked_at",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    purpose: Mapped[UserActionTokenPurpose] = mapped_column(
        SqlEnum(
            UserActionTokenPurpose,
            name="user_action_token_purpose",
            values_callable=lambda enum_type: [member.value for member in enum_type],
        ),
        nullable=False,
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
