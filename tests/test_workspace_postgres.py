"""Opt-in membership races; never use an application database."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.core.security import AccessTokenClaims, AccessTokenService, hash_token
from app.modules.identity.models import User, UserSession, UserStatus
from app.modules.workspace.model import Membership, MembershipRole, MembershipStatus, Workspace
from app.modules.workspace.repository import WorkspaceRepository
from app.modules.workspace.schemas import MembershipResponse, MembershipVersionRequest
from app.modules.workspace.service import (
    LastWorkspaceOwnerError,
    MembershipVersionConflictError,
    WorkspaceService,
)


@pytest.fixture
async def membership_postgres_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    dsn = os.environ.get("CAMPAIGNHUB_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set CAMPAIGNHUB_TEST_DATABASE_URL to a dedicated *_test database")
    url = make_url(dsn)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").endswith("_test"):
        pytest.fail("Membership races require postgresql+asyncpg and a database ending in _test")
    schema = "membership_test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    created = False
    try:
        async with admin.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()
        if created:
            # Only this fixture's generated isolated schema is dropped.
            async with admin.begin() as connection:
                await connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.dispose()


async def seed_members(
    factory: async_sessionmaker[AsyncSession],
    *,
    two_owners: bool,
) -> tuple[UUID, list[UUID], list[AccessTokenClaims]]:
    now = datetime.now(UTC)
    tokens = AccessTokenService(secret_key=SecretStr("membership-postgres-test-signing-key-123456"))
    member_ids = []
    claims = []
    async with factory() as session, session.begin():
        workspace = Workspace(name="Concurrent workspace")
        session.add(workspace)
        await session.flush()
        for index in range(2):
            email = f"race{index}@example.com"
            user = User(
                email=email,
                normalized_email=email,
                password_hash="test",
                status=UserStatus.ACTIVE,
                email_verified_at=now,
            )
            session.add(user)
            await session.flush()
            login = UserSession(
                user_id=user.id,
                security_version=user.security_version,
                refresh_token_hash=hash_token(f"race-refresh-{index}"),
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(days=1),
            )
            member = Membership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=MembershipRole.OWNER if index == 0 or two_owners else MembershipRole.MARKETER,
                status=MembershipStatus.ACTIVE,
                accepted_at=now,
            )
            session.add_all([login, member])
            await session.flush()
            member_ids.append(member.id)
            claims.append(
                tokens.decode(
                    tokens.create(
                        user_id=user.id, session_id=login.id, security_version=user.security_version
                    )
                )
            )
        workspace_id = workspace.id
    return workspace_id, member_ids, claims


async def test_competing_owner_departures_leave_one_eligible_owner(
    membership_postgres_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = membership_postgres_sessions
    workspace_id, _, actors = await seed_members(factory, two_owners=True)
    barrier = asyncio.Barrier(2)

    async def leave(actor: AccessTokenClaims) -> MembershipResponse | LastWorkspaceOwnerError:
        async with factory() as session:
            service = WorkspaceService(session)
            original_lock = service.repository.lock_workspace

            async def synchronized_lock(workspace_id: UUID) -> Workspace | None:
                await barrier.wait()
                return await original_lock(workspace_id)

            monkeypatch.setattr(service.repository, "lock_workspace", synchronized_lock)
            try:
                return await service.leave_workspace(
                    workspace_id, MembershipVersionRequest(expected_version=1), actor=actor
                )
            except LastWorkspaceOwnerError as exc:
                return exc

    async with asyncio.timeout(15):
        results = await asyncio.gather(*(leave(actor) for actor in actors))
    assert sum(isinstance(result, MembershipResponse) for result in results) == 1
    assert sum(isinstance(result, LastWorkspaceOwnerError) for result in results) == 1
    async with factory() as session:
        assert await WorkspaceRepository(session).count_eligible_owners(workspace_id) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.workspace_id == workspace_id,
                    Membership.status == MembershipStatus.REMOVED,
                )
            )
            == 1
        )


async def test_parallel_membership_updates_reject_the_stale_version(
    membership_postgres_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = membership_postgres_sessions
    workspace_id, members, actors = await seed_members(factory, two_owners=False)
    barrier = asyncio.Barrier(2)

    async def suspend() -> MembershipResponse | MembershipVersionConflictError:
        async with factory() as session:
            service = WorkspaceService(session)
            original_lookup = service.repository.get_membership_by_id

            async def synchronized_lookup(
                *, workspace_id: UUID, membership_id: UUID
            ) -> Membership | None:
                result = await original_lookup(
                    workspace_id=workspace_id, membership_id=membership_id
                )
                await barrier.wait()
                return result

            monkeypatch.setattr(service.repository, "get_membership_by_id", synchronized_lookup)
            try:
                return await service.suspend_membership(
                    workspace_id,
                    members[1],
                    MembershipVersionRequest(expected_version=1),
                    actor=actors[0],
                )
            except MembershipVersionConflictError as exc:
                return exc

    async with asyncio.timeout(15):
        results = await asyncio.gather(suspend(), suspend())
    assert sum(isinstance(result, MembershipResponse) for result in results) == 1
    assert sum(isinstance(result, MembershipVersionConflictError) for result in results) == 1
    async with factory() as session:
        member = await session.get(Membership, members[1])
        assert (
            member is not None
            and member.status == MembershipStatus.SUSPENDED
            and member.version == 2
        )
