from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import (
    AuthenticationRequiredError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    InvalidStateError,
)
from app.core.security import (
    AccessTokenClaims,
    AccessTokenService,
    InvalidAccessTokenError,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.modules.identity.models import (
    UserActionToken,
    UserActionTokenPurpose,
    UserSession,
    UserStatus,
)
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.schemas import (
    EmailVerificationConfirmRequest,
    EmailVerificationRequest,
    LoginRequest,
    LogoutRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)


class EmailAlreadyRegisteredError(ConflictError):
    default_code = "EMAIL_ALREADY_REGISTERED"
    default_message = "An account with this email already exists"


class InvalidVerificationTokenError(BadRequestError):
    default_code = "INVALID_VERIFICATION_TOKEN"
    default_message = "The verification token is invalid or no longer usable"


class InvalidPasswordResetTokenError(BadRequestError):
    default_code = "INVALID_PASSWORD_RESET_TOKEN"
    default_message = "The password-reset token is invalid or no longer usable."


class invalidCredentialsError(AuthenticationRequiredError):
    default_code = "INVALID_CREDENTIALS"
    default_message = "Invalid email or password"


class AccountNotEligibleError(ForbiddenError):
    default_code = "ACCOUNT_NOT_ELIGIBLE"
    default_message = "This account is not permitted to log in"


class InvalidRefreshTokenError(AuthenticationRequiredError):
    default_code = "INVALID_REFRESH_TOKEN"
    default_message = "The refresh token is invalid or no longer usable."


class RefreshTokenReuseError(AuthenticationRequiredError):
    default_code = "REFRESH_TOKEN_REUSED"
    default_message = "This session is no longer usable. Please log in again."


@dataclass(frozen=True)
class RegistrationResult:
    user: UserResponse
    verification_token: SecretStr = field(repr=False)
    verification_expires_at: datetime


@dataclass(frozen=True)
class ActionTokenDelivery:
    """Private delivery data; never serialize this as the public API response."""

    email: str = field(repr=False)
    token: SecretStr = field(repr=False)
    expires_at: datetime


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def _is_duplicate_email_error(error: IntegrityError) -> bool:
    current: BaseException | None = error.orig

    while current is not None:
        if (
            getattr(current, "sqlstate", None) == "23505"
            and getattr(current, "constraint_name", None) == "uq_users_normalized_email"
        ):
            return True

        current = current.__cause__
    return False


class IdentityService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        verification_token_lifetime: timedelta = timedelta(hours=24),
        password_reset_token_lifetime: timedelta = timedelta(hours=1),
    ) -> None:
        if verification_token_lifetime <= timedelta(0):
            raise ValueError("Verification-token lifetime must be positive")
        if password_reset_token_lifetime <= timedelta(0):
            raise ValueError("Password-reset-token lifetime must be positive")
        self.session = session
        self.repository = IdentityRepository(session)
        self.verification_token_lifetime = verification_token_lifetime
        self.password_reset_token_lifetime = password_reset_token_lifetime

    async def register(self, command: RegisterRequest) -> RegistrationResult:
        email = str(command.email)
        normalized_email = normalize_email(email)

        password_hash = await run_in_threadpool(hash_password, command.password.get_secret_value())

        raw_token = generate_opaque_token()

        try:
            async with self.session.begin():
                existing_user = await self.repository.get_user_by_email(normalized_email)

                if existing_user:
                    raise EmailAlreadyRegisteredError()

                user = await self.repository.create_user(
                    email=email, normalized_email=normalized_email, password_hash=password_hash
                )

                now = datetime.now(UTC)
                expires_at = now + self.verification_token_lifetime

                await self.repository.add_action_token(
                    UserActionToken(
                        user_id=user.id,
                        purpose=(UserActionTokenPurpose.EMAIL_VERIFICATION),
                        token_hash=hash_token(raw_token),
                        created_at=now,
                        updated_at=now,
                        expires_at=expires_at,
                    )
                )

                result = RegistrationResult(
                    user=UserResponse(
                        id=user.id,
                        email=user.email,
                        status=user.status,
                        email_verified=user.email_verified_at is not None,
                    ),
                    verification_token=SecretStr(raw_token),
                    verification_expires_at=expires_at,
                )

            return result

        except IntegrityError as exc:
            if _is_duplicate_email_error(exc):
                raise EmailAlreadyRegisteredError() from None
            raise

    async def request_email_verification(
        self, command: EmailVerificationRequest
    ) -> ActionTokenDelivery | None:
        """Replace a pending account's token; None means no delivery is needed.

        The eventual route must return the same public response for either outcome.
        This method creates a token, not an email or a durable delivery job.
        """
        normalized_email = normalize_email(str(command.email))
        async with self.session.begin():
            candidate = await self.repository.get_user_by_email(normalized_email)
            if candidate is None:
                return None
            user = await self.repository.lock_user(candidate.id)
            if (
                user is None
                or user.normalized_email != normalized_email
                or user.status != UserStatus.PENDING
                or user.email_verified_at is not None
            ):
                return None

            now = datetime.now(UTC)
            purpose = UserActionTokenPurpose.EMAIL_VERIFICATION
            await self.repository.revoke_open_action_tokens(
                user_id=user.id, purpose=purpose, now=now
            )
            raw_token = generate_opaque_token()
            expires_at = now + self.verification_token_lifetime
            await self.repository.add_action_token(
                UserActionToken(
                    user_id=user.id,
                    purpose=purpose,
                    token_hash=hash_token(raw_token),
                    created_at=now,
                    updated_at=now,
                    expires_at=expires_at,
                )
            )
            delivery = ActionTokenDelivery(
                email=user.email, token=SecretStr(raw_token), expires_at=expires_at
            )
        return delivery

    async def request_password_reset(
        self, command: PasswordResetRequest
    ) -> ActionTokenDelivery | None:
        """Prepare private delivery data; public responses must not reveal eligibility."""
        email = normalize_email(str(command.email))
        async with self.session.begin():
            candidate = await self.repository.get_user_by_email(email)
            if candidate is None:
                return None
            user = await self.repository.lock_user(candidate.id)
            if (
                user is None
                or user.normalized_email != email
                or user.status != UserStatus.ACTIVE
                or user.email_verified_at is None
            ):
                return None
            now = datetime.now(UTC)
            purpose = UserActionTokenPurpose.PASSWORD_RESET
            await self.repository.revoke_open_action_tokens(
                user_id=user.id, purpose=purpose, now=now
            )
            raw_token = generate_opaque_token()
            expires_at = now + self.password_reset_token_lifetime
            await self.repository.add_action_token(
                UserActionToken(
                    user_id=user.id,
                    purpose=purpose,
                    token_hash=hash_token(raw_token),
                    created_at=now,
                    updated_at=now,
                    expires_at=expires_at,
                )
            )
            delivery = ActionTokenDelivery(
                email=user.email, token=SecretStr(raw_token), expires_at=expires_at
            )
        return delivery

    async def reset_password(self, command: PasswordResetConfirmRequest) -> None:
        """Consume a reset token, change the password and revoke sessions atomically."""
        password_hash = await run_in_threadpool(
            hash_password, command.new_password.get_secret_value()
        )
        async with self.session.begin():
            purpose = UserActionTokenPurpose.PASSWORD_RESET
            token = await self.repository.get_action_token_by_hash(
                token_hash=hash_token(command.token.get_secret_value()), purpose=purpose
            )
            if token is None:
                raise InvalidPasswordResetTokenError()
            user = await self.repository.lock_user(token.user_id)
            if user is None or user.status != UserStatus.ACTIVE or user.email_verified_at is None:
                raise InvalidPasswordResetTokenError()
            now = datetime.now(UTC)
            if not await self.repository.consume_action_token(
                token_id=token.id, user_id=user.id, purpose=purpose, now=now
            ):
                raise InvalidPasswordResetTokenError()
            user.password_hash = password_hash
            user.security_version += 1
            await self.repository.revoke_all_sessions(
                user_id=user.id, now=now, reason="password_reset"
            )
            await self.session.flush()

    async def get_me(self, claims: AccessTokenClaims) -> UserResponse:
        """Read current public user data after rechecking authenticated claims."""
        async with self.session.begin():
            await self._validate_access_claims(claims)
            user = await self.repository.get_user_by_id(claims.sub)
            if user is None:
                raise AuthenticationRequiredError()
            return UserResponse(
                id=user.id, email=user.email, status=user.status, email_verified=True
            )

    async def verify_email(self, command: EmailVerificationConfirmRequest) -> UserResponse:
        token_hash = hash_token(command.token.get_secret_value())

        async with self.session.begin():
            token = await self.repository.get_action_token_by_hash(
                token_hash=token_hash,
                purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
            )
            if token is None:
                raise InvalidVerificationTokenError()

            user = await self.repository.lock_user(token.user_id)
            if user is None:
                raise InvalidVerificationTokenError()

            now = datetime.now(UTC)

            consumed = await self.repository.consume_action_token(
                token_id=token.id,
                user_id=user.id,
                purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
                now=now,
            )

            if not consumed:
                raise InvalidVerificationTokenError()

            if user.status != UserStatus.PENDING:
                raise InvalidStateError(
                    message="This account cannot be activated through email verification"
                )

            if user.email_verified_at is not None:
                raise InvalidStateError(message="This account's email is already verified.")
            user.status = UserStatus.ACTIVE
            user.email_verified_at = now

            await self.session.flush()
            result = UserResponse(
                id=user.id, email=user.email, status=user.status, email_verified=True
            )
        return result

    async def login(
        self,
        command: LoginRequest,
        *,
        access_token_service: AccessTokenService,
        refresh_token_lifetime: timedelta = timedelta(days=30),
    ) -> TokenResponse:
        if refresh_token_lifetime <= timedelta(0):
            raise ValueError("Refresh-token lifetime must be positive")
        normalized_email = normalize_email(str(command.email))
        password = command.password.get_secret_value()

        async with self.session.begin():
            user = await self.repository.get_user_by_email(normalized_email)

            if user is None:
                snapshot = None

            else:
                snapshot = (user.id, user.password_hash, user.security_version)
        if snapshot is None:
            await run_in_threadpool(hash_password, password)
            raise invalidCredentialsError()

        user_id, stored_password_hash, security_version = snapshot

        password_matches = await run_in_threadpool(verify_password, password, stored_password_hash)

        if not password_matches:
            raise invalidCredentialsError()

        async with self.session.begin():
            current_user = await self.repository.lock_user(user_id)

            if current_user is None:
                raise invalidCredentialsError()

            # Reject credentials invalidated while password verification ran.
            if (
                current_user.password_hash != stored_password_hash
                or current_user.security_version != security_version
            ):
                raise invalidCredentialsError()

            if current_user.status != UserStatus.ACTIVE or current_user.email_verified_at is None:
                raise AccountNotEligibleError()

            now = datetime.now(UTC)
            raw_refresh_token = generate_opaque_token()

            refresh_session = await self.repository.add_session(
                UserSession(
                    user_id=current_user.id,
                    security_version=current_user.security_version,
                    refresh_token_hash=hash_token(raw_refresh_token),
                    issued_at=now,
                    expires_at=now + refresh_token_lifetime,
                )
            )
            current_user.last_login_at = now
            access_token = access_token_service.create(
                user_id=current_user.id,
                security_version=current_user.security_version,
                session_id=refresh_session.id,
            )
            await self.session.flush()

            result = TokenResponse(
                access_token=access_token,
                refresh_token=raw_refresh_token,
                expires_in=access_token_service.lifetime_seconds,
                user=UserResponse(
                    id=current_user.id,
                    email=current_user.email,
                    status=current_user.status,
                    email_verified=True,
                ),
            )
        return result

    async def refresh_tokens(
        self, command: RefreshTokenRequest, *, access_token_service: AccessTokenService
    ) -> TokenResponse:
        result: TokenResponse | None = None
        replay_detected = False
        async with self.session.begin():
            refresh_session = await self.repository.get_session_by_hash(
                hash_token(command.refresh_token.get_secret_value())
            )
            if refresh_session is None:
                raise InvalidRefreshTokenError()
            user = await self.repository.lock_user(refresh_session.user_id)
            if user is None:
                raise InvalidRefreshTokenError()
            await self.session.refresh(refresh_session)
            now = datetime.now(UTC)
            if refresh_session.revoked_at is not None:
                raise InvalidRefreshTokenError()
            if refresh_session.rotated_at is not None:
                await self.repository.revoke_token_family(
                    user_id=user.id,
                    token_family_id=refresh_session.token_family_id,
                    now=now,
                    reason="refresh_token_reuse",
                )
                replay_detected = True
            else:
                if refresh_session.security_version != user.security_version:
                    raise InvalidRefreshTokenError()
                if user.status != UserStatus.ACTIVE or user.email_verified_at is None:
                    raise AccountNotEligibleError()
                if not await self.repository.mark_session_rotated(
                    session_id=refresh_session.id, user_id=user.id, now=now
                ):
                    raise InvalidRefreshTokenError()
                raw_token = generate_opaque_token()
                replacement = await self.repository.add_session(
                    UserSession(
                        user_id=user.id,
                        token_family_id=refresh_session.token_family_id,
                        security_version=user.security_version,
                        refresh_token_hash=hash_token(raw_token),
                        issued_at=now,
                        expires_at=refresh_session.expires_at,
                    )
                )
                result = TokenResponse(
                    access_token=access_token_service.create(
                        user_id=user.id,
                        session_id=replacement.id,
                        security_version=user.security_version,
                    ),
                    refresh_token=raw_token,
                    expires_in=access_token_service.lifetime_seconds,
                    user=UserResponse(
                        id=user.id, email=user.email, status=user.status, email_verified=True
                    ),
                )
        # Raising before transaction exit would undo the security action.
        if replay_detected:
            raise RefreshTokenReuseError()
        if result is None:
            raise RuntimeError("Refresh completed without a token response")
        return result

    async def authenticate_access_token(
        self, token: str, *, access_token_service: AccessTokenService
    ) -> AccessTokenClaims:
        try:
            claims = access_token_service.decode(token)
        except InvalidAccessTokenError:
            raise AuthenticationRequiredError() from None
        async with self.session.begin():
            await self._validate_access_claims(claims)
        return claims

    async def _validate_access_claims(self, claims: AccessTokenClaims) -> None:
        # Lock order is always user first. The lock also coordinates with
        # refresh, logout and future password/status-changing workflows.
        user = await self.repository.lock_user(claims.sub)
        if (
            user is None
            or user.status != UserStatus.ACTIVE
            or user.email_verified_at is None
            or user.security_version != claims.security_version
        ):
            raise AuthenticationRequiredError()
        if not await self.repository.access_session_is_valid(
            session_id=claims.sid,
            user_id=claims.sub,
            security_version=claims.security_version,
            now=datetime.now(UTC),
        ):
            raise AuthenticationRequiredError()

    async def logout(self, command: LogoutRequest) -> None:
        """Idempotently revoke the family identified by a refresh-token credential."""
        async with self.session.begin():
            refresh_session = await self.repository.get_session_by_hash(
                hash_token(command.refresh_token.get_secret_value())
            )
            if refresh_session is None:
                return

            # Same lock order as refresh: user first, then session changes.
            user = await self.repository.lock_user(refresh_session.user_id)
            if user is None:
                return
            await self.session.refresh(refresh_session)

            # Even rotated/expired tokens may revoke their own family.
            # No account-status check: a suspended user can still log out.
            # The repository leaves already-revoked rows unchanged.
            await self.repository.revoke_token_family(
                user_id=user.id,
                token_family_id=refresh_session.token_family_id,
                now=datetime.now(UTC),
                reason="logout",
            )

    async def logout_all(self, claims: AccessTokenClaims) -> None:
        """Accept only claims from authenticate_access_token, never client JSON."""
        async with self.session.begin():
            # Recheck under the lock; the earlier dependency check is not
            # authorization to act after a concurrent security change.
            await self._validate_access_claims(claims)
            user = await self.repository.get_user_by_id(claims.sub)
            if user is None:
                raise AuthenticationRequiredError()
            user.security_version += 1
            await self.repository.revoke_all_sessions(
                user_id=user.id, now=datetime.now(UTC), reason="logout_all"
            )
            await self.session.flush()
