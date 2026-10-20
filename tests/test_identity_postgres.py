"""Opt-in locking tests against a dedicated PostgreSQL test database."""

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.core.exceptions import AuthenticationRequiredError
from app.core.security import AccessTokenService
from app.modules.identity.models import User, UserActionToken, UserActionTokenPurpose, UserSession
from app.modules.identity.schemas import (
    EmailVerificationConfirmRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
)
from app.modules.identity.service import (
    IdentityService,
    InvalidPasswordResetTokenError,
    RefreshTokenReuseError,
)


@pytest.fixture
async def postgres_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    dsn = os.environ.get("CAMPAIGNHUB_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set CAMPAIGNHUB_TEST_DATABASE_URL to a dedicated *_test database")
    url = make_url(dsn)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").endswith("_test"):
        pytest.fail("Concurrency tests require postgresql+asyncpg and a database ending in _test")
    schema = "identity_test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    created = False
    try:
        async with admin.begin() as conn:
            await conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()
        if created:
            # Only this fixture's randomly named schema is removed.
            async with admin.begin() as conn:
                await conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.dispose()


async def test_parallel_refresh_detects_replay_and_commits_family_revocation(
    postgres_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = AccessTokenService(secret_key=SecretStr("test-only-postgres-signing-key-123456789"))
    async with postgres_sessions() as session:
        service = IdentityService(session)
        registered = await service.register(
            RegisterRequest(email="owner@example.com", password=SecretStr("a-long-test-password"))
        )
        await service.verify_email(
            EmailVerificationConfirmRequest(token=registered.verification_token)
        )
        issued = await service.login(
            LoginRequest(email="owner@example.com", password=SecretStr("a-long-test-password")),
            access_token_service=tokens,
        )

    # Both requests read the same unused token before either locks the user.
    barrier = asyncio.Barrier(2)

    async def refresh() -> TokenResponse | RefreshTokenReuseError:
        async with postgres_sessions() as session:
            service = IdentityService(session)
            original = service.repository.get_session_by_hash

            async def synchronized_lookup(token_hash: str) -> UserSession | None:
                result = await original(token_hash)
                await barrier.wait()
                return result

            monkeypatch.setattr(service.repository, "get_session_by_hash", synchronized_lookup)
            try:
                return await service.refresh_tokens(
                    RefreshTokenRequest(refresh_token=SecretStr(issued.refresh_token)),
                    access_token_service=tokens,
                )
            except RefreshTokenReuseError as exc:
                return exc

    async with asyncio.timeout(15):
        results = await asyncio.gather(refresh(), refresh())
    assert sum(isinstance(result, TokenResponse) for result in results) == 1
    assert sum(isinstance(result, RefreshTokenReuseError) for result in results) == 1
    winner = next(result for result in results if isinstance(result, TokenResponse))
    async with postgres_sessions() as session:
        service = IdentityService(session)
        for token in (issued.access_token, winner.access_token):
            with pytest.raises(AuthenticationRequiredError):
                await service.authenticate_access_token(token, access_token_service=tokens)


async def test_parallel_password_reset_consumes_token_exactly_once(
    postgres_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with postgres_sessions() as session:
        service = IdentityService(session)
        registered = await service.register(
            RegisterRequest(email="reset@example.com", password=SecretStr("a-long-test-password"))
        )
        await service.verify_email(
            EmailVerificationConfirmRequest(token=registered.verification_token)
        )
        delivery = await service.request_password_reset(
            PasswordResetRequest(email="reset@example.com")
        )
        assert delivery is not None
        command = PasswordResetConfirmRequest(
            token=delivery.token, new_password=SecretStr("another-long-test-password")
        )
    barrier = asyncio.Barrier(2)

    async def reset() -> bool:
        async with postgres_sessions() as session:
            service = IdentityService(session)
            original = service.repository.get_action_token_by_hash

            async def synchronized_lookup(
                *, token_hash: str, purpose: UserActionTokenPurpose
            ) -> UserActionToken | None:
                token = await original(token_hash=token_hash, purpose=purpose)
                await barrier.wait()
                return token

            monkeypatch.setattr(service.repository, "get_action_token_by_hash", synchronized_lookup)
            try:
                await service.reset_password(command)
                return True
            except InvalidPasswordResetTokenError:
                return False

    async with asyncio.timeout(15):
        results = await asyncio.gather(reset(), reset())
    assert sorted(results) == [False, True]
    async with postgres_sessions() as session:
        user = await session.get(User, registered.user.id)
        assert user is not None
        assert user.security_version == 2
