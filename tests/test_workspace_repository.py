"""SQLite repository behavior tests; these do not verify PostgreSQL row locking."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database import Base
from app.modules.identity.models import User
from app.modules.workspace.model import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Workspace,
    WorkspaceStatus,
)
from app.modules.workspace.repository import WorkspaceRepository


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


async def create_user(session: AsyncSession) -> User:
    email = f"{uuid4().hex}@example.com"
    user = User(email=email, normalized_email=email, password_hash="test-hash")
    session.add(user)
    await session.flush()
    return user


async def create_accessible_workspace(
    session: AsyncSession,
    user: User,
    *,
    name: str = "Business",
    role: MembershipRole = MembershipRole.OWNER,
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
) -> tuple[Workspace, Membership]:
    repository = WorkspaceRepository(session)
    workspace = await repository.create_workspace(name=name)
    workspace.status = status
    membership = await repository.create_membership(
        workspace_id=workspace.id,
        user_id=user.id,
        role=role,
        status=MembershipStatus.ACTIVE,
        accepted_at=datetime.now(UTC),
    )
    return workspace, membership


async def test_creation_flushes_without_committing(session: AsyncSession) -> None:
    user = await create_user(session)
    workspace, membership = await create_accessible_workspace(session, user)
    assert workspace.id is not None and membership.id is not None
    assert workspace.timezone == "Africa/Lagos"
    assert workspace.default_currency == "NGN"
    assert membership.accepted_at is not None
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(Workspace)) == 0
    assert await session.scalar(select(func.count()).select_from(Membership)) == 0


async def test_workspace_lookup_and_listing_are_user_scoped(session: AsyncSession) -> None:
    first_user, other_user = await create_user(session), await create_user(session)
    first, _ = await create_accessible_workspace(session, first_user)
    other, _ = await create_accessible_workspace(session, other_user)
    repository = WorkspaceRepository(session)
    assert (
        await repository.get_workspace_for_user(workspace_id=first.id, user_id=first_user.id)
        is first
    )
    assert (
        await repository.get_workspace_for_user(workspace_id=other.id, user_id=first_user.id)
        is None
    )
    assert (
        await repository.get_workspace_for_user(workspace_id=uuid4(), user_id=first_user.id) is None
    )
    assert await repository.list_workspaces_for_user(user_id=first_user.id) == [first]


@pytest.mark.parametrize("state", ["invited", "suspended", "removed", "closed", "archived"])
async def test_inaccessible_workspaces_are_excluded(session: AsyncSession, state: str) -> None:
    user = await create_user(session)
    workspace, membership = await create_accessible_workspace(session, user)
    if state == "closed":
        workspace.status = WorkspaceStatus.CLOSED
    elif state == "archived":
        workspace.archived_at = datetime.now(UTC)
    else:
        membership.status = MembershipStatus(state)
        if state == "removed":
            membership.removed_at = datetime.now(UTC)
    await session.flush()
    repository = WorkspaceRepository(session)
    assert (
        await repository.get_workspace_for_user(workspace_id=workspace.id, user_id=user.id) is None
    )
    assert await repository.list_workspaces_for_user(user_id=user.id) == []


async def test_optional_filters_narrow_accessible_workspaces(session: AsyncSession) -> None:
    user = await create_user(session)
    matching, _ = await create_accessible_workspace(session, user, name="Lagos Fashion")
    await create_accessible_workspace(
        session, user, name="Fashion Team", role=MembershipRole.MARKETER
    )
    await create_accessible_workspace(
        session, user, name="Fashion Pending", status=WorkspaceStatus.PENDING
    )
    await create_accessible_workspace(session, user, name="Other Business")
    other_user = await create_user(session)
    await create_accessible_workspace(session, other_user, name="Lagos Fashion")
    repository = WorkspaceRepository(session)
    assert await repository.list_workspaces_for_user(
        user_id=user.id,
        status=WorkspaceStatus.ACTIVE,
        role=MembershipRole.OWNER,
        search=" FASHION ",
    ) == [matching]
    assert (
        await repository.list_workspaces_for_user(user_id=user.id, status=WorkspaceStatus.CLOSED)
        == []
    )


@pytest.mark.parametrize("search,name", [("%", "100% Fashion"), ("_", "Shop_Name")])
async def test_search_treats_wildcards_as_literal_characters(
    session: AsyncSession, search: str, name: str
) -> None:
    user = await create_user(session)
    matching, _ = await create_accessible_workspace(session, user, name=name)
    await create_accessible_workspace(session, user, name="Other Shop")
    repository = WorkspaceRepository(session)
    assert await repository.list_workspaces_for_user(user_id=user.id, search=search) == [matching]
    assert await repository.list_workspaces_for_user(user_id=user.id, search="' OR 1=1 --") == []


async def test_blank_search_does_not_hide_results(session: AsyncSession) -> None:
    user = await create_user(session)
    workspace, _ = await create_accessible_workspace(session, user)
    assert await WorkspaceRepository(session).list_workspaces_for_user(
        user_id=user.id, search="   "
    ) == [workspace]


async def test_pagination_uses_id_to_break_timestamp_ties(session: AsyncSession) -> None:
    user = await create_user(session)
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    workspaces = []
    for _ in range(3):
        workspace, _membership = await create_accessible_workspace(session, user)
        workspace.created_at = timestamp
        workspaces.append(workspace)
    await session.flush()
    repository = WorkspaceRepository(session)
    first = await repository.list_workspaces_for_user(user_id=user.id, limit=2)
    second = await repository.list_workspaces_for_user(
        user_id=user.id, limit=2, after=(first[-1].created_at, first[-1].id)
    )
    expected = sorted(workspace.id for workspace in workspaces)
    assert [workspace.id for workspace in first + second] == expected
    assert (
        await repository.list_workspaces_for_user(
            user_id=user.id, after=(second[-1].created_at, second[-1].id)
        )
        == []
    )


@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_invalid_page_size_is_rejected(session: AsyncSession, limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        await WorkspaceRepository(session).list_workspaces_for_user(user_id=uuid4(), limit=limit)


async def test_overlong_search_is_rejected(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="200"):
        await WorkspaceRepository(session).list_workspaces_for_user(
            user_id=uuid4(), search="x" * 201
        )


async def test_membership_lookups_and_locks_are_workspace_scoped(session: AsyncSession) -> None:
    user = await create_user(session)
    workspace, membership = await create_accessible_workspace(session, user)
    repository = WorkspaceRepository(session)
    assert await repository.get_membership(workspace_id=workspace.id, user_id=user.id) is membership
    assert (
        await repository.get_membership_by_id(
            workspace_id=workspace.id, membership_id=membership.id
        )
        is membership
    )
    assert await repository.lock_workspace(workspace.id) is workspace
    assert await repository.lock_workspace(uuid4()) is None
    assert (
        await repository.lock_membership(workspace_id=workspace.id, membership_id=membership.id)
        is membership
    )
    for wrong_workspace in (uuid4(),):
        assert (
            await repository.get_membership(workspace_id=wrong_workspace, user_id=user.id) is None
        )
        assert (
            await repository.get_membership_by_id(
                workspace_id=wrong_workspace, membership_id=membership.id
            )
            is None
        )
        assert (
            await repository.lock_membership(
                workspace_id=wrong_workspace, membership_id=membership.id
            )
            is None
        )


async def test_lock_queries_refresh_stale_loaded_values(session: AsyncSession) -> None:
    user = await create_user(session)
    workspace, membership = await create_accessible_workspace(session, user)
    await session.execute(
        update(Workspace)
        .where(Workspace.id == workspace.id)
        .values(name="Updated")
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(Membership)
        .where(Membership.id == membership.id)
        .values(role=MembershipRole.ANALYST)
        .execution_options(synchronize_session=False)
    )
    repository = WorkspaceRepository(session)
    await repository.lock_workspace(workspace.id)
    await repository.lock_membership(workspace_id=workspace.id, membership_id=membership.id)
    assert workspace.name == "Updated"
    assert membership.role == MembershipRole.ANALYST


async def test_owner_count_excludes_inactive_owners_and_other_workspaces(
    session: AsyncSession,
) -> None:
    repository = WorkspaceRepository(session)
    owner = await create_user(session)
    workspace, _ = await create_accessible_workspace(session, owner)
    second_owner = await create_user(session)
    await repository.create_membership(
        workspace_id=workspace.id, user_id=second_owner.id, role=MembershipRole.OWNER
    )
    marketer = await create_user(session)
    await repository.create_membership(
        workspace_id=workspace.id,
        user_id=marketer.id,
        role=MembershipRole.MARKETER,
        status=MembershipStatus.ACTIVE,
        accepted_at=datetime.now(UTC),
    )
    await create_accessible_workspace(session, owner)
    assert await repository.count_active_owners(workspace.id) == 1
    assert await repository.count_active_owners(uuid4()) == 0


async def test_flush_persists_changes_without_committing(session: AsyncSession) -> None:
    user = await create_user(session)
    workspace, _ = await create_accessible_workspace(session, user)
    previous_version = workspace.version
    workspace.name = "Changed"
    await WorkspaceRepository(session).flush()
    assert workspace.version == previous_version + 1
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(Workspace)) == 0
