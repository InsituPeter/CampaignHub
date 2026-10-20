from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database import Base
from app.core.exceptions import AuthenticationRequiredError, InvalidStateError
from app.core.security import AccessTokenService, hash_token, verify_password
from app.modules.identity.dependencies import get_current_identity
from app.modules.identity.models import (
    User,
    UserActionToken,
    UserActionTokenPurpose,
    UserSession,
    UserStatus,
)
from app.modules.identity.schemas import (
    EmailVerificationConfirmRequest,
    EmailVerificationRequest,
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
)
from app.modules.identity.service import (
    AccountNotEligibleError,
    EmailAlreadyRegisteredError,
    IdentityService,
    InvalidPasswordResetTokenError,
    InvalidRefreshTokenError,
    InvalidVerificationTokenError,
    RefreshTokenReuseError,
    _is_duplicate_email_error,
    normalize_email,
)

PASSWORD = "a-long-registration-password"


async def test_password_reset_replaces_password_and_revokes_all_sessions(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    delivery = await service.request_password_reset(PasswordResetRequest(email="OWNER@example.com"))
    assert delivery is not None
    # Merely requesting a reset does not log the user out.
    await service.authenticate_access_token(issued.access_token, access_token_service=access_tokens)
    async with session.begin():
        token = await service.repository.get_action_token_by_hash(
            token_hash=hash_token(delivery.token.get_secret_value()),
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
        )
        assert token is not None
        assert token.expires_at - token.created_at == timedelta(hours=1)
        assert token.token_hash != delivery.token.get_secret_value()
    command = PasswordResetConfirmRequest(
        token=delivery.token, new_password=SecretStr("my-new-secure-password")
    )
    await service.reset_password(command)
    assert not session.in_transaction()
    with pytest.raises(InvalidPasswordResetTokenError):
        await service.reset_password(command)
    with pytest.raises(AuthenticationRequiredError):
        await service.authenticate_access_token(
            issued.access_token, access_token_service=access_tokens
        )
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh_tokens(
            RefreshTokenRequest(refresh_token=SecretStr(issued.refresh_token)),
            access_token_service=access_tokens,
        )
    with pytest.raises(AuthenticationRequiredError):
        await service.login(login_command(), access_token_service=access_tokens)
    new = await service.login(
        login_command(password="my-new-secure-password"), access_token_service=access_tokens
    )
    claims = access_tokens.decode(new.access_token)
    assert claims.sub == user_id
    assert claims.security_version == 2


async def test_password_reset_request_replaces_previous_token(session: AsyncSession) -> None:
    service = IdentityService(session)
    await activate_user(service)
    first = await service.request_password_reset(PasswordResetRequest(email="owner@example.com"))
    second = await service.request_password_reset(PasswordResetRequest(email="owner@example.com"))
    assert first is not None and second is not None
    with pytest.raises(InvalidPasswordResetTokenError):
        await service.reset_password(
            PasswordResetConfirmRequest(
                token=first.token, new_password=SecretStr("my-new-secure-password")
            )
        )
    await service.reset_password(
        PasswordResetConfirmRequest(
            token=second.token, new_password=SecretStr("my-new-secure-password")
        )
    )


@pytest.mark.parametrize("state", ["unknown", "pending", "suspended", "disabled", "unverified"])
async def test_password_reset_request_ignores_ineligible_accounts(
    session: AsyncSession, state: str
) -> None:
    service = IdentityService(session)
    if state != "unknown":
        user_id = await activate_user(service)
        async with session.begin():
            user = await session.get(User, user_id)
            assert user is not None
            if state == "unverified":
                user.email_verified_at = None
            else:
                user.status = UserStatus(state)
    assert (
        await service.request_password_reset(PasswordResetRequest(email="owner@example.com"))
        is None
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(UserActionToken)
            .where(UserActionToken.purpose == UserActionTokenPurpose.PASSWORD_RESET)
        )
        == 0
    )


@pytest.mark.parametrize("state", ["expired", "revoked", "wrong_purpose", "suspended"])
async def test_password_reset_confirmation_rejects_unusable_tokens(
    session: AsyncSession, access_tokens: AccessTokenService, state: str
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    delivery = await service.request_password_reset(PasswordResetRequest(email="owner@example.com"))
    assert delivery is not None
    async with session.begin():
        token = await service.repository.get_action_token_by_hash(
            token_hash=hash_token(delivery.token.get_secret_value()),
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
        )
        user = await session.get(User, user_id)
        assert token is not None and user is not None
        if state == "expired":
            token.created_at = datetime.now(UTC) - timedelta(days=2)
            token.expires_at = datetime.now(UTC) - timedelta(days=1)
        elif state == "revoked":
            token.revoked_at = datetime.now(UTC)
        elif state == "wrong_purpose":
            token.purpose = UserActionTokenPurpose.EMAIL_VERIFICATION
        else:
            user.status = UserStatus.SUSPENDED
    with pytest.raises(InvalidPasswordResetTokenError):
        await service.reset_password(
            PasswordResetConfirmRequest(
                token=delivery.token, new_password=SecretStr("my-new-secure-password")
            )
        )
    user = await session.get(User, user_id)
    assert user is not None
    assert user.security_version == 1
    assert verify_password(PASSWORD, user.password_hash)


async def test_password_reset_failure_rolls_back_password_token_and_revocations(
    session: AsyncSession, access_tokens: AccessTokenService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    delivery = await service.request_password_reset(PasswordResetRequest(email="owner@example.com"))
    assert delivery is not None
    original = service.repository.revoke_all_sessions

    async def fail_after_revoke(*, user_id: UUID, now: datetime, reason: str) -> None:
        await original(user_id=user_id, now=now, reason=reason)
        raise RuntimeError("revocation failed")

    monkeypatch.setattr(service.repository, "revoke_all_sessions", fail_after_revoke)
    command = PasswordResetConfirmRequest(
        token=delivery.token, new_password=SecretStr("my-new-secure-password")
    )
    with pytest.raises(RuntimeError, match="revocation failed"):
        await service.reset_password(command)
    await service.authenticate_access_token(issued.access_token, access_token_service=access_tokens)
    await service.login(login_command(), access_token_service=access_tokens)
    monkeypatch.setattr(service.repository, "revoke_all_sessions", original)
    await service.reset_password(command)


@pytest.mark.parametrize("lifetime", [timedelta(0), timedelta(seconds=-1)])
async def test_password_reset_rejects_invalid_lifetime(
    session: AsyncSession, lifetime: timedelta
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        IdentityService(session, password_reset_token_lifetime=lifetime)


@pytest.mark.parametrize("expired", [False, True])
async def test_verification_resend_replaces_old_token_atomically(
    session: AsyncSession, expired: bool
) -> None:
    lifetime = timedelta(minutes=45)
    service = IdentityService(session, verification_token_lifetime=lifetime)
    registered = await service.register(registration())
    if expired:
        async with session.begin():
            old = await session.scalar(select(UserActionToken))
            assert old is not None
            old.created_at = datetime.now(UTC) - timedelta(days=2)
            old.expires_at = datetime.now(UTC) - timedelta(days=1)
    delivery = await service.request_email_verification(
        EmailVerificationRequest(email="OWNER@EXAMPLE.COM")
    )
    assert delivery is not None
    assert not session.in_transaction()
    assert delivery.email == registered.user.email
    assert delivery.token != registered.verification_token
    assert delivery.token.get_secret_value() not in repr(delivery)
    assert delivery.email not in repr(delivery)
    async with session.begin():
        old = await service.repository.get_action_token_by_hash(
            token_hash=hash_token(registered.verification_token.get_secret_value()),
            purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
        )
        new = await service.repository.get_action_token_by_hash(
            token_hash=hash_token(delivery.token.get_secret_value()),
            purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
        )
        assert old is not None and new is not None
        await session.refresh(old)
        assert old.revoked_at is not None
        assert old.consumed_at is None
        assert new.user_id == registered.user.id
        assert new.revoked_at is None and new.consumed_at is None
        assert new.token_hash != delivery.token.get_secret_value()
        assert new.expires_at - new.created_at == lifetime
        assert new.expires_at.replace(tzinfo=UTC) == delivery.expires_at
    with pytest.raises(InvalidVerificationTokenError):
        await service.verify_email(
            EmailVerificationConfirmRequest(token=registered.verification_token)
        )
    result = await service.verify_email(EmailVerificationConfirmRequest(token=delivery.token))
    assert result.status == UserStatus.ACTIVE


@pytest.mark.parametrize(
    ("status", "verified"),
    [
        (UserStatus.ACTIVE, True),
        (UserStatus.ACTIVE, False),
        (UserStatus.SUSPENDED, False),
        (UserStatus.DISABLED, False),
        (UserStatus.PENDING, True),
    ],
)
async def test_verification_resend_ignores_ineligible_accounts(
    session: AsyncSession, status: UserStatus, verified: bool
) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    async with session.begin():
        user = await session.get(User, registered.user.id)
        assert user is not None
        user.status = status
        user.email_verified_at = datetime.now(UTC) if verified else None
    assert (
        await service.request_email_verification(
            EmailVerificationRequest(email="owner@example.com")
        )
        is None
    )
    assert not session.in_transaction()
    session.expunge_all()
    token = await session.scalar(select(UserActionToken))
    assert token is not None
    assert token.revoked_at is None
    assert await session.scalar(select(func.count()).select_from(UserActionToken)) == 1


async def test_verification_resend_unknown_email_is_noop(session: AsyncSession) -> None:
    assert (
        await IdentityService(session).request_email_verification(
            EmailVerificationRequest(email="unknown@example.com")
        )
        is None
    )
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(User)) == 0
    assert await session.scalar(select(func.count()).select_from(UserActionToken)) == 0


async def test_verification_resend_failure_restores_original_token(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    monkeypatch.setattr(
        service.repository, "add_action_token", AsyncMock(side_effect=RuntimeError("failure"))
    )
    with pytest.raises(RuntimeError, match="failure"):
        await service.request_email_verification(
            EmailVerificationRequest(email="owner@example.com")
        )
    assert not session.in_transaction()
    result = await service.verify_email(
        EmailVerificationConfirmRequest(token=registered.verification_token)
    )
    assert result.status == UserStatus.ACTIVE
    assert await session.scalar(select(func.count()).select_from(UserActionToken)) == 1


async def test_verification_resend_preserves_password_reset_tokens(session: AsyncSession) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    async with session.begin():
        now = datetime.now(UTC)
        reset = await service.repository.add_action_token(
            UserActionToken(
                user_id=registered.user.id,
                purpose=UserActionTokenPurpose.PASSWORD_RESET,
                token_hash=hash_token("unrelated-reset-token"),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        reset_id = reset.id
    await service.request_email_verification(EmailVerificationRequest(email="owner@example.com"))
    session.expunge_all()
    stored_reset = await session.get(UserActionToken, reset_id)
    assert stored_reset is not None
    assert stored_reset.revoked_at is None


async def test_logout_revokes_rotated_family_but_preserves_other_sessions(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    first = await service.login(login_command(), access_token_service=access_tokens)
    other = await service.login(login_command(), access_token_service=access_tokens)
    replacement = await service.refresh_tokens(
        RefreshTokenRequest(refresh_token=SecretStr(first.refresh_token)),
        access_token_service=access_tokens,
    )
    # A rotated token can still log out its own family.
    await service.logout(LogoutRequest(refresh_token=SecretStr(first.refresh_token)))
    assert not session.in_transaction()
    for issued in (first, replacement):
        with pytest.raises(AuthenticationRequiredError):
            await service.authenticate_access_token(
                issued.access_token, access_token_service=access_tokens
            )
        with pytest.raises(InvalidRefreshTokenError):
            await service.refresh_tokens(
                RefreshTokenRequest(refresh_token=SecretStr(issued.refresh_token)),
                access_token_service=access_tokens,
            )
    await service.authenticate_access_token(other.access_token, access_token_service=access_tokens)
    await service.refresh_tokens(
        RefreshTokenRequest(refresh_token=SecretStr(other.refresh_token)),
        access_token_service=access_tokens,
    )
    async with session.begin():
        user = await session.get(User, user_id)
        assert user is not None
        assert user.security_version == 1


async def test_logout_is_repeatable_and_preserves_revocation_timestamp(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    command = LogoutRequest(refresh_token=SecretStr(issued.refresh_token))
    await service.logout(command)
    async with session.begin():
        stored = await service.repository.get_session_by_hash(hash_token(issued.refresh_token))
        assert stored is not None
        await session.refresh(stored)
        revoked_at = stored.revoked_at
        assert revoked_at is not None
        assert stored.revocation_reason == "logout"
    await service.logout(command)
    assert not session.in_transaction()
    session.expunge_all()
    stored = await service.repository.get_session_by_hash(hash_token(issued.refresh_token))
    assert stored is not None
    assert stored.revoked_at == revoked_at
    assert stored.revocation_reason == "logout"


async def test_logout_unknown_token_does_not_revoke_existing_session(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    await service.logout(LogoutRequest(refresh_token=SecretStr("unknown-token")))
    assert not session.in_transaction()
    await service.authenticate_access_token(issued.access_token, access_token_service=access_tokens)


@pytest.mark.parametrize("state", ["expired", "suspended"])
async def test_logout_does_not_require_an_active_unexpired_account_session(
    session: AsyncSession, access_tokens: AccessTokenService, state: str
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    async with session.begin():
        stored = await service.repository.get_session_by_hash(hash_token(issued.refresh_token))
        assert stored is not None
        if state == "expired":
            stored.issued_at = datetime.now(UTC) - timedelta(days=2)
            stored.expires_at = datetime.now(UTC) - timedelta(days=1)
        else:
            user = await session.get(User, user_id)
            assert user is not None
            user.status = UserStatus.SUSPENDED
    await service.logout(LogoutRequest(refresh_token=SecretStr(issued.refresh_token)))
    session.expunge_all()
    stored = await service.repository.get_session_by_hash(hash_token(issued.refresh_token))
    assert stored is not None
    assert stored.revoked_at is not None
    assert stored.revocation_reason == "logout"


async def test_logout_failure_rolls_back_revocation(
    session: AsyncSession, access_tokens: AccessTokenService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    original = service.repository.revoke_token_family

    async def fail_after_revocation(
        *, user_id: UUID, token_family_id: UUID, now: datetime, reason: str
    ) -> None:
        await original(user_id=user_id, token_family_id=token_family_id, now=now, reason=reason)
        raise RuntimeError("revocation failed")

    monkeypatch.setattr(service.repository, "revoke_token_family", fail_after_revocation)
    with pytest.raises(RuntimeError, match="revocation failed"):
        await service.logout(LogoutRequest(refresh_token=SecretStr(issued.refresh_token)))
    assert not session.in_transaction()
    await service.authenticate_access_token(issued.access_token, access_token_service=access_tokens)
    await service.refresh_tokens(
        RefreshTokenRequest(refresh_token=SecretStr(issued.refresh_token)),
        access_token_service=access_tokens,
    )


async def test_identity_dependency_requires_bearer_and_checks_database(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    with pytest.raises(AuthenticationRequiredError):
        await get_current_identity(None, session, access_tokens)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=issued.access_token)
    claims = await get_current_identity(credentials, session, access_tokens)
    assert not session.in_transaction()
    await service.logout_all(claims)
    with pytest.raises(AuthenticationRequiredError):
        await get_current_identity(credentials, session, access_tokens)


@pytest.mark.parametrize(
    "invalid", ["malformed", "missing_session", "wrong_owner", "wrong_version"]
)
async def test_access_authentication_rejects_invalid_session_binding(
    session: AsyncSession, access_tokens: AccessTokenService, invalid: str
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    claims = access_tokens.decode(issued.access_token)
    if invalid == "wrong_owner":
        async with session.begin():
            other = await service.repository.create_user(
                email="other@example.com",
                normalized_email="other@example.com",
                password_hash="unused",
            )
            other.status = UserStatus.ACTIVE
            other.email_verified_at = datetime.now(UTC)
            user_id = other.id
    token = (
        "malformed"
        if invalid == "malformed"
        else access_tokens.create(
            user_id=user_id,
            session_id=uuid4() if invalid == "missing_session" else claims.sid,
            security_version=2 if invalid == "wrong_version" else 1,
        )
    )
    with pytest.raises(AuthenticationRequiredError):
        await service.authenticate_access_token(token, access_token_service=access_tokens)


async def test_refresh_rotates_with_fixed_expiry_and_replay_revokes_only_its_family(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    first = await service.login(login_command(), access_token_service=access_tokens)
    other = await service.login(login_command(), access_token_service=access_tokens)
    replacement = await service.refresh_tokens(
        RefreshTokenRequest(refresh_token=SecretStr(first.refresh_token)),
        access_token_service=access_tokens,
    )
    assert replacement.refresh_token != first.refresh_token
    for token in (first.access_token, replacement.access_token):
        await service.authenticate_access_token(token, access_token_service=access_tokens)
    async with session.begin():
        old = await service.repository.get_session_by_hash(hash_token(first.refresh_token))
        new = await service.repository.get_session_by_hash(hash_token(replacement.refresh_token))
        assert old is not None and new is not None
        assert old.rotated_at is not None
        assert new.token_family_id == old.token_family_id
        assert new.expires_at == old.expires_at
        assert new.security_version == old.security_version
        assert access_tokens.decode(replacement.access_token).sid == new.id
    with pytest.raises(RefreshTokenReuseError):
        await service.refresh_tokens(
            RefreshTokenRequest(refresh_token=SecretStr(first.refresh_token)),
            access_token_service=access_tokens,
        )
    assert not session.in_transaction()
    for token in (first.access_token, replacement.access_token):
        with pytest.raises(AuthenticationRequiredError):
            await service.authenticate_access_token(token, access_token_service=access_tokens)
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh_tokens(
            RefreshTokenRequest(refresh_token=SecretStr(replacement.refresh_token)),
            access_token_service=access_tokens,
        )
    await service.authenticate_access_token(other.access_token, access_token_service=access_tokens)


@pytest.mark.parametrize("state", ["expired", "revoked", "version", "suspended", "unknown"])
async def test_refresh_rejects_invalid_state(
    session: AsyncSession, access_tokens: AccessTokenService, state: str
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    login = await service.login(login_command(), access_token_service=access_tokens)
    async with session.begin():
        stored = await service.repository.get_session_by_hash(hash_token(login.refresh_token))
        user = await session.get(User, user_id)
        assert stored is not None and user is not None
        if state == "expired":
            stored.issued_at = datetime.now(UTC) - timedelta(days=2)
            stored.expires_at = datetime.now(UTC) - timedelta(days=1)
        elif state == "revoked":
            stored.revoked_at = datetime.now(UTC)
        elif state == "version":
            user.security_version += 1
        elif state == "suspended":
            user.status = UserStatus.SUSPENDED
    expected = AccountNotEligibleError if state == "suspended" else InvalidRefreshTokenError
    with pytest.raises(expected):
        await service.refresh_tokens(
            RefreshTokenRequest(
                refresh_token=SecretStr("unknown" if state == "unknown" else login.refresh_token)
            ),
            access_token_service=access_tokens,
        )
    if state != "unknown":
        with pytest.raises(AuthenticationRequiredError):
            await service.authenticate_access_token(
                login.access_token, access_token_service=access_tokens
            )
    assert await session.scalar(select(func.count()).select_from(UserSession)) == 1


async def test_refresh_signing_failure_rolls_back_rotation(
    session: AsyncSession, access_tokens: AccessTokenService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    login = await service.login(login_command(), access_token_service=access_tokens)

    def fail_create(
        self: AccessTokenService, *, user_id: UUID, session_id: UUID, security_version: int
    ) -> str:
        raise RuntimeError("signing failed")

    monkeypatch.setattr(AccessTokenService, "create", fail_create)
    with pytest.raises(RuntimeError, match="signing failed"):
        await service.refresh_tokens(
            RefreshTokenRequest(refresh_token=SecretStr(login.refresh_token)),
            access_token_service=access_tokens,
        )
    session.expunge_all()
    stored = await service.repository.get_session_by_hash(hash_token(login.refresh_token))
    assert stored is not None
    assert stored.rotated_at is None
    assert await session.scalar(select(func.count()).select_from(UserSession)) == 1


async def test_logout_all_revokes_access_and_refresh_tokens(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    first = await service.login(login_command(), access_token_service=access_tokens)
    second = await service.login(login_command(), access_token_service=access_tokens)
    claims = await service.authenticate_access_token(
        first.access_token, access_token_service=access_tokens
    )
    await service.logout_all(claims)
    for issued in (first, second):
        with pytest.raises(AuthenticationRequiredError):
            await service.authenticate_access_token(
                issued.access_token, access_token_service=access_tokens
            )
        with pytest.raises(InvalidRefreshTokenError):
            await service.refresh_tokens(
                RefreshTokenRequest(refresh_token=SecretStr(issued.refresh_token)),
                access_token_service=access_tokens,
            )
    # A new login captures the new version and works normally.
    third = await service.login(login_command(), access_token_service=access_tokens)
    new_claims = await service.authenticate_access_token(
        third.access_token, access_token_service=access_tokens
    )
    assert new_claims.sub == user_id
    assert new_claims.security_version == claims.security_version + 1


async def test_logout_all_failure_rolls_back_version_change(
    session: AsyncSession, access_tokens: AccessTokenService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    issued = await service.login(login_command(), access_token_service=access_tokens)
    claims = await service.authenticate_access_token(
        issued.access_token, access_token_service=access_tokens
    )
    monkeypatch.setattr(
        service.repository, "revoke_all_sessions", AsyncMock(side_effect=RuntimeError("failure"))
    )
    with pytest.raises(RuntimeError, match="failure"):
        await service.logout_all(claims)
    await service.authenticate_access_token(issued.access_token, access_token_service=access_tokens)


@pytest.fixture
def access_tokens() -> AccessTokenService:
    return AccessTokenService(
        secret_key=SecretStr("test-only-signing-key-not-for-production-123456")
    )


async def activate_user(service: IdentityService) -> UUID:
    registered = await service.register(registration())
    await service.verify_email(EmailVerificationConfirmRequest(token=registered.verification_token))
    return registered.user.id


def login_command(email: str = "OWNER@example.com", password: str = PASSWORD) -> LoginRequest:
    return LoginRequest(email=email, password=SecretStr(password))


async def test_login_issues_tokens_and_commits_session(
    session: AsyncSession, access_tokens: AccessTokenService
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    before = datetime.now(UTC)
    result = await service.login(login_command(), access_token_service=access_tokens)
    after = datetime.now(UTC)

    assert not session.in_transaction()
    claims = access_tokens.decode(result.access_token)
    assert claims.sub == user_id
    assert claims.security_version == 1
    assert result.expires_in == access_tokens.lifetime_seconds
    assert result.token_type == "bearer"
    assert result.user.id == user_id
    assert result.user.status == UserStatus.ACTIVE
    assert result.user.email_verified is True
    session.expunge_all()
    user = await session.get(User, user_id)
    stored = await session.scalar(select(UserSession))
    assert user is not None
    assert stored is not None
    assert stored.user_id == user_id
    assert stored.refresh_token_hash == hash_token(result.refresh_token)
    assert stored.refresh_token_hash != result.refresh_token
    assert stored.expires_at - stored.issued_at == timedelta(days=30)
    assert stored.token_family_id is not None
    assert stored.rotated_at is None
    assert stored.revoked_at is None
    assert user.last_login_at is not None
    assert before <= user.last_login_at.replace(tzinfo=UTC) <= after
    assert user.last_login_at == stored.issued_at


@pytest.mark.parametrize("unknown", [True, False])
async def test_login_rejects_bad_credentials_with_same_public_error(
    session: AsyncSession, access_tokens: AccessTokenService, unknown: bool
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)
    command = (
        login_command(email="missing@example.com") if unknown else login_command(password="wrong")
    )
    with pytest.raises(AuthenticationRequiredError) as caught:
        await service.login(command, access_token_service=access_tokens)
    assert caught.value.code == "INVALID_CREDENTIALS"
    assert caught.value.status_code == 401
    assert caught.value.message.rstrip(".") == "Invalid email or password"
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(UserSession)) == 0
    user = await session.get(User, user_id)
    assert user is not None
    assert user.last_login_at is None


@pytest.mark.parametrize(
    ("status", "verified"),
    [
        (UserStatus.PENDING, False),
        (UserStatus.ACTIVE, False),
        (UserStatus.SUSPENDED, True),
        (UserStatus.DISABLED, True),
    ],
)
async def test_login_rejects_ineligible_accounts(
    session: AsyncSession,
    access_tokens: AccessTokenService,
    status: UserStatus,
    verified: bool,
) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    async with session.begin():
        user = await session.get(User, registered.user.id)
        assert user is not None
        user.status = status
        user.email_verified_at = datetime.now(UTC) if verified else None
    with pytest.raises(AccountNotEligibleError):
        await service.login(login_command(), access_token_service=access_tokens)
    assert await session.scalar(select(func.count()).select_from(UserSession)) == 0


@pytest.mark.parametrize("change", ["password_hash", "security_version"])
async def test_login_rechecks_security_state_after_password_verification(
    session: AsyncSession,
    access_tokens: AccessTokenService,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    service = IdentityService(session)
    await activate_user(service)
    original_lock = service.repository.lock_user

    async def changed_user(user_id: UUID) -> User | None:
        # Simulate changed state at the second read; this does not
        # exercise real concurrent PostgreSQL transactions.
        statement = update(User).where(User.id == user_id)
        if change == "password_hash":
            statement = statement.values(password_hash="replacement-hash")
        else:
            statement = statement.values(security_version=User.security_version + 1)
        await session.execute(statement)
        return await original_lock(user_id)

    monkeypatch.setattr(service.repository, "lock_user", changed_user)
    with pytest.raises(AuthenticationRequiredError) as caught:
        await service.login(login_command(), access_token_service=access_tokens)
    assert caught.value.code == "INVALID_CREDENTIALS"
    assert await session.scalar(select(func.count()).select_from(UserSession)) == 0


async def test_login_token_failure_rolls_back_session_and_timestamp(
    session: AsyncSession, access_tokens: AccessTokenService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = IdentityService(session)
    user_id = await activate_user(service)

    def fail_create(
        self: AccessTokenService, *, user_id: UUID, session_id: UUID, security_version: int
    ) -> str:
        raise RuntimeError("token signing failed")

    monkeypatch.setattr(AccessTokenService, "create", fail_create)
    with pytest.raises(RuntimeError, match="token signing failed"):
        await service.login(login_command(), access_token_service=access_tokens)
    assert not session.in_transaction()
    session.expunge_all()
    assert await session.scalar(select(func.count()).select_from(UserSession)) == 0
    user = await session.get(User, user_id)
    assert user is not None
    assert user.last_login_at is None


@pytest.mark.parametrize("lifetime", [timedelta(0), timedelta(seconds=-1)])
async def test_login_rejects_nonpositive_refresh_lifetime(
    session: AsyncSession, access_tokens: AccessTokenService, lifetime: timedelta
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        await IdentityService(session).login(
            login_command(), access_token_service=access_tokens, refresh_token_lifetime=lifetime
        )
    assert not session.in_transaction()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, autoflush=False, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


def registration(email: str = "Owner@example.com") -> RegisterRequest:
    return RegisterRequest(email=email, password=SecretStr(PASSWORD))


async def test_registration_commits_pending_user_and_hashed_token(session: AsyncSession) -> None:
    before = datetime.now(UTC)
    result = await IdentityService(session).register(registration())
    after = datetime.now(UTC)

    assert not session.in_transaction()
    # Remove cached objects and verify persisted database values.
    session.expunge_all()
    user = await session.get(User, result.user.id)
    assert user is not None
    assert user.normalized_email == "owner@example.com"
    assert user.status == UserStatus.PENDING
    assert user.email_verified_at is None
    assert user.password_hash != PASSWORD
    assert verify_password(PASSWORD, user.password_hash)
    assert result.user.status == UserStatus.PENDING
    assert result.user.email_verified is False

    token = await session.scalar(select(UserActionToken))
    assert token is not None
    raw_token = result.verification_token.get_secret_value()
    assert token.user_id == user.id
    assert token.purpose == UserActionTokenPurpose.EMAIL_VERIFICATION
    assert token.token_hash == hash_token(raw_token)
    assert token.token_hash != raw_token
    assert token.consumed_at is None
    assert token.revoked_at is None
    assert token.expires_at - token.created_at == timedelta(hours=24)
    assert before + timedelta(hours=24) <= result.verification_expires_at
    assert result.verification_expires_at <= after + timedelta(hours=24)
    # SQLite returns naive datetimes for these timezone-aware columns.
    assert token.expires_at.replace(tzinfo=UTC) == result.verification_expires_at
    assert raw_token not in repr(result)


async def test_registration_accepts_custom_token_lifetime(session: AsyncSession) -> None:
    lifetime = timedelta(minutes=30)
    await IdentityService(session, verification_token_lifetime=lifetime).register(registration())
    token = await session.scalar(select(UserActionToken))
    assert token is not None
    assert token.expires_at - token.created_at == lifetime


@pytest.mark.parametrize("email", ["Owner@example.com", "OWNER@EXAMPLE.COM"])
async def test_duplicate_registration_preserves_original_records(
    session: AsyncSession, email: str
) -> None:
    service = IdentityService(session)
    original = await service.register(registration())

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register(registration(email))

    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(User)) == 1
    assert await session.scalar(select(func.count()).select_from(UserActionToken)) == 1
    token = await session.scalar(select(UserActionToken))
    assert token is not None
    assert token.user_id == original.user.id
    assert token.token_hash == hash_token(original.verification_token.get_secret_value())


async def test_token_failure_rolls_back_user_and_propagates_integrity_error(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = IdentityService(session)
    failure = IntegrityError("INSERT", {}, RuntimeError("unrelated constraint"))
    monkeypatch.setattr(service.repository, "add_action_token", AsyncMock(side_effect=failure))

    with pytest.raises(IntegrityError) as caught:
        await service.register(registration())

    assert caught.value is failure
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(User)) == 0
    assert await session.scalar(select(func.count()).select_from(UserActionToken)) == 0


@pytest.mark.parametrize("lifetime", [timedelta(0), timedelta(seconds=-1)])
async def test_nonpositive_token_lifetime_is_rejected(
    session: AsyncSession, lifetime: timedelta
) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        IdentityService(session, verification_token_lifetime=lifetime)


def test_email_normalization() -> None:
    assert normalize_email("  OWNER@Example.com  ") == "owner@example.com"


@pytest.mark.parametrize(
    ("sqlstate", "constraint_name", "expected"),
    [
        ("23505", "uq_users_normalized_email", True),
        ("23505", "uq_user_action_tokens_token_hash", False),
        ("23514", "uq_users_normalized_email", False),
    ],
)
def test_duplicate_email_classifier_checks_wrapped_database_error(
    sqlstate: str, constraint_name: str, expected: bool
) -> None:
    # Simulates asyncpg's wrapped error; not a PostgreSQL concurrency test.
    class DatabaseError(Exception):
        pass

    original = DatabaseError()
    original.sqlstate = sqlstate  # type: ignore[attr-defined]
    original.constraint_name = constraint_name  # type: ignore[attr-defined]
    wrapper = RuntimeError("driver wrapper")
    wrapper.__cause__ = original
    error = IntegrityError("INSERT", {}, wrapper)
    assert _is_duplicate_email_error(error) is expected


def test_unrelated_integrity_error_is_not_duplicate_email() -> None:
    error = IntegrityError("INSERT", {}, RuntimeError("other failure"))
    assert not _is_duplicate_email_error(error)


async def test_email_verification_activates_user_and_consumes_token(session: AsyncSession) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    before = datetime.now(UTC)
    result = await service.verify_email(
        EmailVerificationConfirmRequest(token=registered.verification_token)
    )
    after = datetime.now(UTC)

    assert not session.in_transaction()
    assert result.id == registered.user.id
    assert result.status == UserStatus.ACTIVE
    assert result.email_verified is True
    session.expunge_all()
    user = await session.get(User, result.id)
    token = await session.scalar(select(UserActionToken))
    assert user is not None
    assert token is not None
    assert user.status == UserStatus.ACTIVE
    assert user.email_verified_at is not None
    assert token.consumed_at is not None
    assert before <= user.email_verified_at.replace(tzinfo=UTC) <= after
    assert token.consumed_at == user.email_verified_at
    assert token.revoked_at is None


async def test_email_verification_rejects_reuse_without_changing_activation(
    session: AsyncSession,
) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    command = EmailVerificationConfirmRequest(token=registered.verification_token)
    await service.verify_email(command)
    async with session.begin():
        user = await session.get(User, registered.user.id)
        assert user is not None
        verified_at = user.email_verified_at

    with pytest.raises(InvalidVerificationTokenError):
        await service.verify_email(command)

    session.expunge_all()
    user = await session.get(User, registered.user.id)
    assert user is not None
    assert user.status == UserStatus.ACTIVE
    assert user.email_verified_at == verified_at


@pytest.mark.parametrize("state", ["unknown", "expired", "revoked", "wrong_purpose"])
async def test_email_verification_rejects_unusable_tokens(
    session: AsyncSession, state: str
) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    async with session.begin():
        token = await session.scalar(select(UserActionToken))
        assert token is not None
        if state == "expired":
            token.created_at = datetime.now(UTC) - timedelta(days=2)
            token.expires_at = datetime.now(UTC) - timedelta(days=1)
        elif state == "revoked":
            token.revoked_at = datetime.now(UTC)
        elif state == "wrong_purpose":
            token.purpose = UserActionTokenPurpose.PASSWORD_RESET

    supplied_token = (
        SecretStr("unknown-token") if state == "unknown" else registered.verification_token
    )
    with pytest.raises(InvalidVerificationTokenError):
        await service.verify_email(EmailVerificationConfirmRequest(token=supplied_token))

    assert not session.in_transaction()
    session.expunge_all()
    user = await session.get(User, registered.user.id)
    token = await session.scalar(select(UserActionToken))
    assert user is not None
    assert token is not None
    assert user.status == UserStatus.PENDING
    assert user.email_verified_at is None
    assert token.consumed_at is None


@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.DISABLED, UserStatus.ACTIVE])
async def test_email_verification_preserves_ineligible_account_and_rolls_back_consumption(
    session: AsyncSession, status: UserStatus
) -> None:
    service = IdentityService(session)
    registered = await service.register(registration())
    async with session.begin():
        user = await session.get(User, registered.user.id)
        assert user is not None
        user.status = status

    with pytest.raises(InvalidStateError):
        await service.verify_email(
            EmailVerificationConfirmRequest(token=registered.verification_token)
        )

    assert not session.in_transaction()
    session.expunge_all()
    user = await session.get(User, registered.user.id)
    token = await session.scalar(select(UserActionToken))
    assert user is not None
    assert token is not None
    assert user.status == status
    assert user.email_verified_at is None
    assert token.consumed_at is None
