from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database import Base
from app.modules.identity.models import User, UserActionToken, UserActionTokenPurpose, UserSession
from app.modules.identity.repository import IdentityRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(
            engine,
            autoflush=False,
            expire_on_commit=False,
        ) as database_session:
            yield database_session
    finally:
        await engine.dispose()


@pytest.fixture
def repository(session: AsyncSession) -> IdentityRepository:
    return IdentityRepository(session)


async def create_user(repository: IdentityRepository) -> User:
    return await repository.create_user(
        email="Owner@example.com",
        normalized_email="owner@example.com",
        password_hash="test-password-hash",
    )


async def create_refresh_session(
    repository: IdentityRepository,
    *,
    user_id: UUID,
    now: datetime,
    family_id: UUID | None = None,
) -> UserSession:
    return await repository.add_session(
        UserSession(
            user_id=user_id,
            token_family_id=family_id or uuid4(),
            refresh_token_hash=uuid4().hex,
            issued_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=30),
        )
    )


async def create_action_token(
    repository: IdentityRepository,
    *,
    user_id: UUID,
    now: datetime,
) -> UserActionToken:
    return await repository.add_action_token(
        UserActionToken(
            user_id=user_id,
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
            token_hash=uuid4().hex,
            created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
    )


async def test_user_can_be_found_by_id_and_normalized_email(
    repository: IdentityRepository,
) -> None:
    user = await create_user(repository)
    assert await repository.get_user_by_id(user.id) is user
    assert await repository.get_user_by_email("owner@example.com") is user
    assert await repository.get_user_by_id(uuid4()) is None
    assert await repository.get_user_by_email("missing@example.com") is None


async def test_repository_does_not_commit(
    repository: IdentityRepository,
    session: AsyncSession,
) -> None:
    user = await create_user(repository)
    user_id = user.id
    await session.rollback()
    assert await repository.get_user_by_id(user_id) is None


async def test_refresh_session_can_only_be_rotated_once(
    repository: IdentityRepository,
) -> None:
    user = await create_user(repository)
    now = datetime.now(UTC)
    refresh_session = await create_refresh_session(repository, user_id=user.id, now=now)
    assert await repository.mark_session_rotated(
        session_id=refresh_session.id,
        user_id=user.id,
        now=now,
    )
    assert not await repository.mark_session_rotated(
        session_id=refresh_session.id,
        user_id=user.id,
        now=now,
    )
    stored = await repository.get_session_by_hash(refresh_session.refresh_token_hash)
    assert stored is not None
    assert stored.rotated_at is not None


@pytest.mark.parametrize("state", ["expired", "revoked", "wrong_user"])
async def test_unusable_refresh_session_cannot_rotate(
    repository: IdentityRepository,
    session: AsyncSession,
    state: str,
) -> None:
    user = await create_user(repository)
    now = datetime.now(UTC)
    refresh_session = await create_refresh_session(repository, user_id=user.id, now=now)
    if state == "expired":
        refresh_session.expires_at = now
    elif state == "revoked":
        refresh_session.revoked_at = now
    await session.flush()
    assert not await repository.mark_session_rotated(
        session_id=refresh_session.id,
        user_id=uuid4() if state == "wrong_user" else user.id,
        now=now,
    )


async def test_family_revocation_leaves_other_families_usable(
    repository: IdentityRepository,
) -> None:
    user = await create_user(repository)
    now = datetime.now(UTC)
    family_id = uuid4()
    first = await create_refresh_session(
        repository,
        user_id=user.id,
        now=now,
        family_id=family_id,
    )
    second = await create_refresh_session(
        repository,
        user_id=user.id,
        now=now,
        family_id=family_id,
    )
    other = await create_refresh_session(repository, user_id=user.id, now=now)
    await repository.revoke_token_family(
        user_id=user.id,
        token_family_id=family_id,
        now=now,
        reason="refresh_token_reuse",
    )
    for item in (first, second):
        assert not await repository.mark_session_rotated(
            session_id=item.id,
            user_id=user.id,
            now=now,
        )
    assert await repository.mark_session_rotated(
        session_id=other.id,
        user_id=user.id,
        now=now,
    )


async def test_action_token_can_only_be_consumed_once(repository: IdentityRepository) -> None:
    user = await create_user(repository)
    now = datetime.now(UTC)
    token = await create_action_token(repository, user_id=user.id, now=now)
    assert await repository.consume_action_token(
        token_id=token.id,
        user_id=user.id,
        purpose=UserActionTokenPurpose.PASSWORD_RESET,
        now=now,
    )
    assert not await repository.consume_action_token(
        token_id=token.id,
        user_id=user.id,
        purpose=UserActionTokenPurpose.PASSWORD_RESET,
        now=now,
    )


@pytest.mark.parametrize("state", ["expired", "revoked", "wrong_user", "wrong_purpose"])
async def test_unusable_action_token_cannot_be_consumed(
    repository: IdentityRepository,
    session: AsyncSession,
    state: str,
) -> None:
    user = await create_user(repository)
    now = datetime.now(UTC)
    token = await create_action_token(repository, user_id=user.id, now=now)
    if state == "expired":
        token.expires_at = now
    elif state == "revoked":
        token.revoked_at = now
    await session.flush()
    purpose = (
        UserActionTokenPurpose.EMAIL_VERIFICATION
        if state == "wrong_purpose"
        else UserActionTokenPurpose.PASSWORD_RESET
    )
    assert not await repository.consume_action_token(
        token_id=token.id,
        user_id=uuid4() if state == "wrong_user" else user.id,
        purpose=purpose,
        now=now,
    )


async def test_revoking_expired_action_token_allows_replacement(
    repository: IdentityRepository,
    session: AsyncSession,
) -> None:
    user = await create_user(repository)
    now = datetime.now(UTC)
    old = await create_action_token(repository, user_id=user.id, now=now)
    old.expires_at = now
    await session.flush()
    await repository.revoke_open_action_tokens(
        user_id=user.id,
        purpose=UserActionTokenPurpose.PASSWORD_RESET,
        now=now,
    )
    replacement = await create_action_token(repository, user_id=user.id, now=now)
    assert replacement.id != old.id
    await session.refresh(old)
    assert old.revoked_at is not None
    assert (
        await repository.get_action_token_by_hash(
            token_hash=replacement.token_hash,
            purpose=UserActionTokenPurpose.PASSWORD_RESET,
        )
        is replacement
    )
    assert (
        await repository.get_action_token_by_hash(
            token_hash=replacement.token_hash,
            purpose=UserActionTokenPurpose.EMAIL_VERIFICATION,
        )
        is None
    )
