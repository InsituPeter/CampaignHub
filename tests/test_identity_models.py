from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.identity.models import (
    User,
    UserActionToken,
    UserActionTokenPurpose,
    UserSession,
    UserStatus,
)


@pytest.fixture
def database_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session

    Base.metadata.drop_all(engine)
    engine.dispose()


def create_user(database_session: Session) -> User:
    user = User(
        email="user@example.com",
        normalized_email="user@example.com",
        password_hash="hashed-password",
    )
    database_session.add(user)
    database_session.flush()
    return user


def test_user_receives_expected_defaults(
    database_session: Session,
) -> None:
    user = create_user(database_session)

    assert isinstance(user.id, UUID)
    assert user.status is UserStatus.PENDING
    assert user.security_version == 1
    assert user.created_at is not None
    assert user.updated_at is not None
    assert user.email_verified_at is None
    assert user.last_login_at is None


def test_normalized_email_must_be_unique(
    database_session: Session,
) -> None:
    create_user(database_session)
    duplicate = User(
        email="USER@example.com",
        normalized_email="user@example.com",
        password_hash="another-hashed-password",
    )
    database_session.add(duplicate)

    with pytest.raises(IntegrityError):
        database_session.flush()


def test_security_version_must_be_positive(
    database_session: Session,
) -> None:
    user = User(
        email="invalid@example.com",
        normalized_email="invalid@example.com",
        password_hash="hashed-password",
        security_version=0,
    )
    database_session.add(user)

    with pytest.raises(IntegrityError):
        database_session.flush()


def test_user_session_receives_expected_defaults(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    expires_at = datetime.now(UTC) + timedelta(days=30)
    user_session = UserSession(
        user_id=user.id,
        refresh_token_hash="hashed-refresh-token",
        expires_at=expires_at,
    )
    database_session.add(user_session)
    database_session.flush()

    assert isinstance(user_session.id, UUID)
    assert isinstance(user_session.token_family_id, UUID)
    assert user_session.user_id == user.id
    assert user_session.issued_at is not None
    assert user_session.rotated_at is None
    assert user_session.revoked_at is None


def test_refresh_token_hash_must_be_unique(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    expires_at = datetime.now(UTC) + timedelta(days=30)
    database_session.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash="duplicate-token-hash",
            expires_at=expires_at,
        )
    )
    database_session.flush()
    database_session.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash="duplicate-token-hash",
            expires_at=expires_at,
        )
    )

    with pytest.raises(IntegrityError):
        database_session.flush()


def test_session_expiry_must_follow_issue_time(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    issued_at = datetime.now(UTC)
    database_session.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash="expired-token-hash",
            issued_at=issued_at,
            expires_at=issued_at - timedelta(minutes=1),
        )
    )

    with pytest.raises(IntegrityError):
        database_session.flush()


def test_action_token_stores_only_hashed_token(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    action_token = UserActionToken(
        user_id=user.id,
        purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
        token_hash="hashed-action-token",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    database_session.add(action_token)
    database_session.flush()

    column_names = set(UserActionToken.__table__.columns.keys())

    assert action_token.purpose is UserActionTokenPurpose.EMAIL_VERIFICATION
    assert action_token.consumed_at is None
    assert action_token.revoked_at is None
    assert "token_hash" in column_names
    assert "token" not in column_names
    assert "raw_token" not in column_names


def test_action_token_hash_must_be_unique(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    database_session.add(
        UserActionToken(
            user_id=user.id,
            purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
            token_hash="duplicate-action-token-hash",
            expires_at=expires_at,
        )
    )
    database_session.flush()
    database_session.add(
        UserActionToken(
            user_id=user.id,
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
            token_hash="duplicate-action-token-hash",
            expires_at=expires_at,
        )
    )

    with pytest.raises(IntegrityError):
        database_session.flush()


def test_only_one_unconsumed_token_exists_per_user_and_purpose(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    database_session.add(
        UserActionToken(
            user_id=user.id,
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
            token_hash="first-password-reset-hash",
            expires_at=expires_at,
        )
    )
    database_session.flush()
    database_session.add(
        UserActionToken(
            user_id=user.id,
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
            token_hash="second-password-reset-hash",
            expires_at=expires_at,
        )
    )

    with pytest.raises(IntegrityError):
        database_session.flush()


def test_user_can_have_active_tokens_for_different_purposes(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    database_session.add_all(
        [
            UserActionToken(
                user_id=user.id,
                purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
                token_hash="email-verification-hash",
                expires_at=expires_at,
            ),
            UserActionToken(
                user_id=user.id,
                purpose=UserActionTokenPurpose.PASSWORD_RESET,
                token_hash="password-reset-hash",
                expires_at=expires_at,
            ),
        ]
    )

    database_session.flush()


def test_action_token_expiry_must_follow_creation(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    database_session.add(
        UserActionToken(
            user_id=user.id,
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
            token_hash="expired-action-token-hash",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )

    with pytest.raises(IntegrityError):
        database_session.flush()


def test_action_token_cannot_be_consumed_and_revoked(
    database_session: Session,
) -> None:
    user = create_user(database_session)
    now = datetime.now(UTC)
    database_session.add(
        UserActionToken(
            user_id=user.id,
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
            token_hash="invalid-action-token-hash",
            expires_at=now + timedelta(hours=1),
            consumed_at=now,
            revoked_at=now,
        )
    )

    with pytest.raises(IntegrityError):
        database_session.flush()
