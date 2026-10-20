"""Workspace service tests using SQLite, not PostgreSQL locking integration tests.

PostgreSQL constraint errors are simulated explicitly for error-mapping tests.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm.exc import StaleDataError

from app.core.database import Base
from app.core.exceptions import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    ResourceNotFoundError,
)
from app.core.security import AccessTokenClaims, AccessTokenService, hash_token
from app.modules.identity.models import User, UserSession, UserStatus
from app.modules.workspace import service as workspace_service_module
from app.modules.workspace.model import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Workspace,
    WorkspaceInvitation,
    WorkspaceStatus,
)
from app.modules.workspace.schemas import (
    MembershipResponse,
    MembershipRoleUpdateRequest,
    MembershipVersionRequest,
    OwnerMembershipResponse,
    WorkspaceCreateRequest,
    WorkspaceInvitationAcceptRequest,
    WorkspaceInvitationCreateRequest,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from app.modules.workspace.service import (
    InvalidInvitationError,
    InvitationAlreadyPendingError,
    InvitationCreationResult,
    LastWorkspaceOwnerError,
    MembershipVersionConflictError,
    WorkspaceService,
    WorkspaceSlugTakenError,
    WorkspaceVersionConflictError,
    _is_duplicate_invitation_error,
    _is_duplicate_slug_error,
)


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


@pytest.fixture
async def actor(session: AsyncSession) -> AccessTokenClaims:
    now = datetime.now(UTC)
    async with session.begin():
        user = User(
            email="owner@example.com",
            normalized_email="owner@example.com",
            password_hash="test-hash",
            status=UserStatus.ACTIVE,
            email_verified_at=now,
        )
        session.add(user)
        await session.flush()
        login = UserSession(
            user_id=user.id,
            security_version=user.security_version,
            refresh_token_hash="test-refresh-hash",
            issued_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=1),
        )
        session.add(login)
        await session.flush()
        tokens = AccessTokenService(secret_key=SecretStr("workspace-tests-only-signing-key-123456"))
        return tokens.decode(
            tokens.create(
                user_id=user.id, session_id=login.id, security_version=user.security_version
            )
        )


async def test_creation_commits_workspace_and_active_owner(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    before = datetime.now(UTC)
    result = await WorkspaceService(session).create_workspace(
        WorkspaceCreateRequest(name=" Test Business ", slug=" Test-Business "), actor=actor
    )
    after = datetime.now(UTC)
    assert isinstance(result, WorkspaceResponse)
    assert not session.in_transaction()
    assert result.name == "Test Business"
    assert result.slug == "test-business"
    assert result.status == WorkspaceStatus.PENDING
    assert result.timezone == "Africa/Lagos"
    assert result.default_currency == "NGN"
    session.expunge_all()
    stored = await session.get(Workspace, result.id)
    membership = await session.scalar(
        select(Membership).where(Membership.workspace_id == result.id)
    )
    assert stored is not None
    assert stored.version == 1
    assert membership is not None
    assert membership.user_id == actor.sub
    assert membership.role == MembershipRole.OWNER
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.accepted_at is not None
    assert before <= membership.accepted_at.replace(tzinfo=UTC) <= after
    assert membership.invited_by_user_id is None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 1


@pytest.mark.parametrize("state", ["pending", "suspended", "disabled", "unverified", "version"])
async def test_creation_rejects_ineligible_or_changed_actor(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    async with session.begin():
        user = await session.get(User, actor.sub)
        assert user is not None
        if state == "unverified":
            user.email_verified_at = None
        elif state == "version":
            user.security_version += 1
        else:
            user.status = UserStatus(state)
    with pytest.raises(AuthenticationRequiredError):
        await WorkspaceService(session).create_workspace(
            WorkspaceCreateRequest(name="Business"), actor=actor
        )
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(Workspace)) == 0
    assert await session.scalar(select(func.count()).select_from(Membership)) == 0


@pytest.mark.parametrize(
    "state",
    ["revoked", "expired", "session_version", "missing_session", "missing_user", "expired_jwt"],
)
async def test_creation_rejects_invalid_authentication_state(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    if state in {"revoked", "expired", "session_version"}:
        async with session.begin():
            login = await session.get(UserSession, actor.sid)
            assert login is not None
            if state == "revoked":
                login.revoked_at = datetime.now(UTC)
            elif state == "expired":
                login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                login.security_version += 1
    elif state == "missing_session":
        actor = actor.model_copy(update={"sid": uuid4()})
    elif state == "missing_user":
        actor = actor.model_copy(update={"sub": uuid4()})
    else:
        actor = actor.model_copy(update={"exp": int(datetime.now(UTC).timestamp()) - 1})
    with pytest.raises(AuthenticationRequiredError):
        await WorkspaceService(session).create_workspace(
            WorkspaceCreateRequest(name="Business"), actor=actor
        )
    assert await session.scalar(select(func.count()).select_from(Workspace)) == 0


async def test_membership_failure_rolls_back_workspace(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = WorkspaceService(session)
    failure = IntegrityError("INSERT", {}, RuntimeError("membership constraint"))
    monkeypatch.setattr(service.repository, "create_membership", AsyncMock(side_effect=failure))
    with pytest.raises(IntegrityError) as caught:
        await service.create_workspace(WorkspaceCreateRequest(name="Business"), actor=actor)
    assert caught.value is failure
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(Workspace)) == 0
    assert await session.scalar(select(func.count()).select_from(Membership)) == 0


class DatabaseConstraintError(Exception):
    def __init__(self, sqlstate: str, constraint_name: str) -> None:
        super().__init__("Simulated database constraint")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def constraint_error(sqlstate: str, name: str) -> IntegrityError:
    wrapper = RuntimeError("driver wrapper")
    wrapper.__cause__ = DatabaseConstraintError(sqlstate, name)
    return IntegrityError("INSERT", {}, wrapper)


async def test_slug_constraint_error_maps_to_conflict(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = WorkspaceService(session)
    failure = constraint_error("23505", "uq_workspaces_slug")
    monkeypatch.setattr(service.repository, "create_workspace", AsyncMock(side_effect=failure))
    with pytest.raises(WorkspaceSlugTakenError) as caught:
        await service.create_workspace(
            WorkspaceCreateRequest(name="Business", slug="taken"), actor=actor
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "WORKSPACE_SLUG_TAKEN"
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(Membership)) == 0


@pytest.mark.parametrize(
    "sqlstate,name,expected",
    [
        ("23505", "uq_workspaces_slug", True),
        ("23505", "uq_workspace_memberships_workspace_user", False),
        ("23514", "uq_workspaces_slug", False),
    ],
)
def test_slug_error_classifier_is_constraint_specific(
    sqlstate: str, name: str, expected: bool
) -> None:
    assert _is_duplicate_slug_error(constraint_error(sqlstate, name)) is expected


async def test_multiple_workspaces_can_have_no_slug(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    service = WorkspaceService(session)
    await service.create_workspace(WorkspaceCreateRequest(name="First"), actor=actor)
    await service.create_workspace(WorkspaceCreateRequest(name="Second"), actor=actor)
    assert await session.scalar(select(func.count()).select_from(Workspace)) == 2
    assert await session.scalar(select(func.count()).select_from(Membership)) == 2


async def seed_workspace(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    name: str = "Test workspace",
    role: MembershipRole = MembershipRole.OWNER,
    membership_status: MembershipStatus = MembershipStatus.ACTIVE,
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE,
    archived: bool = False,
    workspace_id: UUID | None = None,
    created_at: datetime | None = None,
) -> UUID:
    """Commit independent test data without relying on the service under test."""
    now = datetime.now(UTC)
    async with session.begin():
        workspace = Workspace(
            id=workspace_id or uuid4(),
            name=name,
            status=status,
            archived_at=now if archived else None,
            created_at=created_at or now,
        )
        session.add(workspace)
        await session.flush()
        if user_id is not None:
            session.add(
                Membership(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    role=role,
                    status=membership_status,
                    accepted_at=now if membership_status != MembershipStatus.INVITED else None,
                    removed_at=now if membership_status == MembershipStatus.REMOVED else None,
                )
            )
        return workspace.id


@pytest.mark.parametrize("role", list(MembershipRole))
@pytest.mark.parametrize(
    "status", [WorkspaceStatus.PENDING, WorkspaceStatus.ACTIVE, WorkspaceStatus.SENDING_SUSPENDED]
)
async def test_get_workspace_returns_public_response_for_active_member(
    session: AsyncSession, actor: AccessTokenClaims, role: MembershipRole, status: WorkspaceStatus
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub, role=role, status=status)

    result = await WorkspaceService(session).get_workspace(workspace_id, actor=actor)

    assert isinstance(result, WorkspaceResponse)
    assert result.id == workspace_id
    assert result.name == "Test workspace"
    assert result.status == status
    assert result.default_currency == "NGN"
    assert not session.in_transaction()


@pytest.mark.parametrize(
    "visibility", ["missing", "nonmember", "invited", "suspended", "removed", "closed", "archived"]
)
async def test_get_and_list_hide_inaccessible_workspaces(
    session: AsyncSession, actor: AccessTokenClaims, visibility: str
) -> None:
    workspace_id = uuid4()
    if visibility != "missing":
        membership_status = (
            MembershipStatus(visibility)
            if visibility in {"invited", "suspended", "removed"}
            else MembershipStatus.ACTIVE
        )
        workspace_id = await seed_workspace(
            session,
            user_id=None if visibility == "nonmember" else actor.sub,
            membership_status=membership_status,
            status=WorkspaceStatus.CLOSED if visibility == "closed" else WorkspaceStatus.ACTIVE,
            archived=visibility == "archived",
        )
    service = WorkspaceService(session)

    with pytest.raises(ResourceNotFoundError) as caught:
        await service.get_workspace(workspace_id, actor=actor)
    assert caught.value.status_code == 404
    assert caught.value.code == "RESOURCE_NOT_FOUND"
    assert not session.in_transaction()
    assert await service.list_workspace(actor=actor) == []
    assert not session.in_transaction()


async def test_list_only_returns_actors_workspaces(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    async with session.begin():
        other_user = User(
            email="other@example.com",
            normalized_email="other@example.com",
            password_hash="test-hash",
            status=UserStatus.ACTIVE,
            email_verified_at=datetime.now(UTC),
        )
        session.add(other_user)
        await session.flush()
        other_id = other_user.id
    own_id = await seed_workspace(session, user_id=actor.sub, name="Own business")
    other_workspace_id = await seed_workspace(session, user_id=other_id, name="Other business")
    service = WorkspaceService(session)

    result = await service.list_workspace(actor=actor)

    assert [workspace.id for workspace in result] == [own_id]
    assert all(isinstance(workspace, WorkspaceResponse) for workspace in result)
    assert not session.in_transaction()
    with pytest.raises(ResourceNotFoundError):
        await service.get_workspace(other_workspace_id, actor=actor)


async def test_list_filters_combine_without_weakening_access_restrictions(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    match = await seed_workspace(
        session, user_id=actor.sub, name="Lagos Shop", role=MembershipRole.MARKETER
    )
    await seed_workspace(session, user_id=actor.sub, name="Lagos Owner")
    await seed_workspace(
        session,
        user_id=actor.sub,
        name="Lagos Pending",
        role=MembershipRole.MARKETER,
        status=WorkspaceStatus.PENDING,
    )
    await seed_workspace(
        session, user_id=actor.sub, name="Abuja Shop", role=MembershipRole.MARKETER
    )
    await seed_workspace(session, user_id=None, name="Lagos Private")
    await seed_workspace(
        session,
        user_id=actor.sub,
        name="Lagos Archived",
        role=MembershipRole.MARKETER,
        archived=True,
    )
    await seed_workspace(
        session,
        user_id=actor.sub,
        name="Lagos Invitation",
        role=MembershipRole.MARKETER,
        membership_status=MembershipStatus.INVITED,
    )

    service = WorkspaceService(session)
    results = await service.list_workspace(
        actor=actor, status=WorkspaceStatus.ACTIVE, role=MembershipRole.MARKETER, search=" lAgOs "
    )

    assert [workspace.id for workspace in results] == [match]
    assert await service.list_workspace(actor=actor, search="not-a-match") == []
    assert not session.in_transaction()


async def test_list_paginates_equal_timestamps_by_id_without_duplicates(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [UUID(int=value) for value in (1, 2, 3)]
    for workspace_id in (ids[2], ids[0], ids[1]):
        await seed_workspace(
            session, user_id=actor.sub, workspace_id=workspace_id, created_at=created_at
        )
    service = WorkspaceService(session)

    first = await service.list_workspace(actor=actor, limit=2)
    assert [workspace.id for workspace in first] == ids[:2]
    second = await service.list_workspace(
        actor=actor, limit=2, after=(first[-1].created_at, first[-1].id)
    )
    assert [workspace.id for workspace in second] == ids[2:]
    assert (
        await service.list_workspace(
            actor=actor, limit=2, after=(second[-1].created_at, second[-1].id)
        )
        == []
    )
    assert not session.in_transaction()


@pytest.mark.parametrize("operation", ["get", "list", "update", "invite"])
@pytest.mark.parametrize(
    "state", ["expired_jwt", "revoked", "expired_session", "security_version", "suspended"]
)
async def test_workspace_operations_reject_invalid_authentication(
    session: AsyncSession, actor: AccessTokenClaims, operation: str, state: str
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    if state == "expired_jwt":
        actor = actor.model_copy(update={"exp": int(datetime.now(UTC).timestamp()) - 1})
    else:
        async with session.begin():
            if state in {"revoked", "expired_session"}:
                login = await session.get(UserSession, actor.sid)
                assert login is not None
                if state == "revoked":
                    login.revoked_at = datetime.now(UTC)
                else:
                    login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                user = await session.get(User, actor.sub)
                assert user is not None
                if state == "security_version":
                    user.security_version += 1
                else:
                    user.status = UserStatus.SUSPENDED
    service = WorkspaceService(session)

    with pytest.raises(AuthenticationRequiredError):
        if operation == "get":
            await service.get_workspace(workspace_id, actor=actor)
        elif operation == "update":
            await service.update_workspace(
                workspace_id,
                WorkspaceUpdateRequest(expected_version=1, name="Not allowed"),
                actor=actor,
            )
        elif operation == "invite":
            await service.create_invitation(
                workspace_id,
                WorkspaceInvitationCreateRequest.model_validate(
                    {"email": "teammate@example.com", "role": "marketer"}
                ),
                actor=actor,
            )
        else:
            await service.list_workspace(actor=actor)
    assert not session.in_transaction()


@pytest.mark.parametrize(
    "status", [WorkspaceStatus.PENDING, WorkspaceStatus.ACTIVE, WorkspaceStatus.SENDING_SUSPENDED]
)
async def test_owner_update_commits_changes_and_increments_version(
    session: AsyncSession, actor: AccessTokenClaims, status: WorkspaceStatus
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub, status=status)

    result = await WorkspaceService(session).update_workspace(
        workspace_id,
        WorkspaceUpdateRequest(
            expected_version=1,
            name=" Revised business ",
            slug=" Revised-Business ",
            timezone="UTC",
            default_currency="NGN",
        ),
        actor=actor,
    )

    assert not session.in_transaction()
    assert isinstance(result, WorkspaceResponse)
    assert result.id == workspace_id
    assert result.name == "Revised business"
    assert result.slug == "revised-business"
    assert result.timezone == "UTC"
    assert result.default_currency == "NGN"
    assert result.version == 2
    assert result.status == status
    assert result.updated_at >= result.created_at.replace(microsecond=0)
    session.expunge_all()
    stored = await session.get(Workspace, workspace_id)
    assert stored is not None
    assert stored.name == result.name
    assert stored.slug == result.slug
    assert stored.timezone == result.timezone
    assert stored.version == 2
    membership = await session.scalar(
        select(Membership).where(Membership.workspace_id == workspace_id)
    )
    assert membership is not None
    assert membership.role == MembershipRole.OWNER
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.version == 1


async def test_partial_update_preserves_omitted_fields_and_allows_clearing_slug(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    async with session.begin():
        workspace = await session.get(Workspace, workspace_id)
        assert workspace is not None
        workspace.slug = "original-slug"
        workspace.timezone = "UTC"
        await session.flush()
        version = workspace.version
    service = WorkspaceService(session)

    renamed = await service.update_workspace(
        workspace_id,
        WorkspaceUpdateRequest(expected_version=version, name="New name"),
        actor=actor,
    )
    assert renamed.slug == "original-slug"
    assert renamed.timezone == "UTC"
    assert renamed.default_currency == "NGN"

    cleared = await service.update_workspace(
        workspace_id,
        WorkspaceUpdateRequest(expected_version=renamed.version, slug=None),
        actor=actor,
    )
    assert cleared.slug is None
    assert cleared.name == "New name"
    assert cleared.timezone == "UTC"
    assert cleared.version == renamed.version + 1
    assert not session.in_transaction()


async def test_noop_update_leaves_version_unchanged(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    result = await WorkspaceService(session).update_workspace(
        workspace_id,
        WorkspaceUpdateRequest(expected_version=1, name="Test workspace"),
        actor=actor,
    )
    assert result.version == 1
    assert not session.in_transaction()


@pytest.mark.parametrize("role", [MembershipRole.MARKETER, MembershipRole.ANALYST])
async def test_update_rejects_nonowners_before_disclosing_version_conflict(
    session: AsyncSession, actor: AccessTokenClaims, role: MembershipRole
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub, role=role)
    with pytest.raises(ForbiddenError) as caught:
        await WorkspaceService(session).update_workspace(
            workspace_id,
            WorkspaceUpdateRequest(expected_version=99, name="Not allowed"),
            actor=actor,
        )
    assert caught.value.status_code == 403
    assert not session.in_transaction()
    stored = await session.get(Workspace, workspace_id)
    assert stored is not None
    assert stored.name == "Test workspace"
    assert stored.version == 1


@pytest.mark.parametrize(
    "visibility", ["missing", "nonmember", "invited", "suspended", "removed", "closed", "archived"]
)
async def test_update_hides_inaccessible_workspaces(
    session: AsyncSession, actor: AccessTokenClaims, visibility: str
) -> None:
    workspace_id = uuid4()
    if visibility != "missing":
        workspace_id = await seed_workspace(
            session,
            user_id=None if visibility == "nonmember" else actor.sub,
            membership_status=(
                MembershipStatus(visibility)
                if visibility in {"invited", "suspended", "removed"}
                else MembershipStatus.ACTIVE
            ),
            status=WorkspaceStatus.CLOSED if visibility == "closed" else WorkspaceStatus.ACTIVE,
            archived=visibility == "archived",
        )

    with pytest.raises(ResourceNotFoundError) as caught:
        await WorkspaceService(session).update_workspace(
            workspace_id,
            WorkspaceUpdateRequest(expected_version=1, name="Not allowed"),
            actor=actor,
        )
    assert caught.value.status_code == 404
    assert not session.in_transaction()
    if visibility != "missing":
        stored = await session.get(Workspace, workspace_id)
        assert stored is not None
        assert stored.name == "Test workspace"
        assert stored.version == 1


async def test_stale_update_preserves_the_previously_committed_change(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    service = WorkspaceService(session)
    first = await service.update_workspace(
        workspace_id, WorkspaceUpdateRequest(expected_version=1, name="First edit"), actor=actor
    )

    with pytest.raises(WorkspaceVersionConflictError) as caught:
        await service.update_workspace(
            workspace_id,
            WorkspaceUpdateRequest(expected_version=1, name="Stale edit", slug="stale-slug"),
            actor=actor,
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "WORKSPACE_VERSION_CONFLICT"
    assert not session.in_transaction()
    stored = await session.get(Workspace, workspace_id)
    assert stored is not None
    assert stored.name == "First edit"
    assert stored.slug is None
    assert stored.version == first.version


@pytest.mark.parametrize("failure_kind", ["slug", "stale", "unrelated"])
async def test_update_maps_errors_and_rolls_back_flushed_changes(
    session: AsyncSession,
    actor: AccessTokenClaims,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    service = WorkspaceService(session)
    failure: Exception
    expected_error: type[Exception]
    if failure_kind == "slug":
        failure = constraint_error("23505", "uq_workspaces_slug")
        expected_error = WorkspaceSlugTakenError
    elif failure_kind == "stale":
        failure = StaleDataError("Simulated version mismatch")
        expected_error = WorkspaceVersionConflictError
    else:
        failure = constraint_error("23514", "ck_workspaces_name_not_blank")
        expected_error = IntegrityError
    original_flush = service.repository.flush

    async def flush_then_fail() -> None:
        # Write changes first, so this verifies rollback rather than only
        # discarding unflushed Python attribute changes.
        await original_flush()
        raise failure

    monkeypatch.setattr(service.repository, "flush", flush_then_fail)
    with pytest.raises(expected_error) as caught:
        await service.update_workspace(
            workspace_id,
            WorkspaceUpdateRequest(expected_version=1, name="Rolled back", slug="new"),
            actor=actor,
        )
    if failure_kind == "unrelated":
        assert caught.value is failure
    assert not session.in_transaction()
    session.expunge_all()
    stored = await session.get(Workspace, workspace_id)
    assert stored is not None
    assert stored.name == "Test workspace"
    assert stored.slug is None
    assert stored.version == 1


def invitation_command(
    email: str = "teammate@example.com", role: str = "marketer"
) -> WorkspaceInvitationCreateRequest:
    return WorkspaceInvitationCreateRequest.model_validate({"email": email, "role": role})


@pytest.mark.parametrize("role", ["marketer", "analyst"])
@pytest.mark.parametrize("expired", [False, True])
async def test_resend_replaces_token_preserves_role_and_restarts_expiry(
    session: AsyncSession, actor: AccessTokenClaims, role: str, expired: bool
) -> None:
    workspace_id, original, recipient = await seed_acceptance(session, actor, role=role)
    if expired:
        async with session.begin():
            row = await session.get(WorkspaceInvitation, original.invitation.id)
            assert row is not None
            row.created_at = datetime.now(UTC) - timedelta(days=3)
            row.expires_at = datetime.now(UTC) - timedelta(days=1)
    service = WorkspaceService(session)
    before = datetime.now(UTC)
    result = await service.resend_invitation(workspace_id, original.invitation.id, actor=actor)
    after = datetime.now(UTC)
    assert not session.in_transaction()
    assert result.invitation.id != original.invitation.id
    assert result.invitation.email == original.invitation.email
    assert result.invitation.role == MembershipRole(role)
    assert result.invitation.invited_by_user_id == actor.sub
    assert before <= result.invitation.created_at <= after
    assert result.invitation.expires_at - result.invitation.created_at == timedelta(hours=48)
    assert result.token.get_secret_value() != original.token.get_secret_value()
    assert result.token.get_secret_value() not in repr(result)
    assert "token_hash" not in result.invitation.model_dump()
    async with session.begin():
        old = await session.get(WorkspaceInvitation, original.invitation.id)
        new = await session.get(WorkspaceInvitation, result.invitation.id)
        assert old is not None and old.revoked_at is not None and old.accepted_at is None
        assert old.token_hash == hash_token(original.token.get_secret_value())
        assert new is not None and new.token_hash == hash_token(result.token.get_secret_value())
        assert await session.scalar(select(func.count()).select_from(Membership)) == 1
        assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 2
    with pytest.raises(InvalidInvitationError):
        await service.accept_invitation(
            WorkspaceInvitationAcceptRequest(token=original.token), actor=recipient
        )
    with pytest.raises(ConflictError):
        await service.resend_invitation(workspace_id, original.invitation.id, actor=actor)
    accepted = await service.accept_invitation(
        WorkspaceInvitationAcceptRequest(token=result.token), actor=recipient
    )
    assert accepted.status == MembershipStatus.ACTIVE
    assert accepted.role == MembershipRole(role)


async def test_another_owner_can_resend_to_an_unregistered_recipient(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id, _, second_owner = await seed_acceptance(session, actor)
    async with session.begin():
        session.add(
            Membership(
                workspace_id=workspace_id,
                user_id=second_owner.sub,
                role=MembershipRole.OWNER,
                status=MembershipStatus.ACTIVE,
                accepted_at=datetime.now(UTC),
            )
        )
    service = WorkspaceService(session)
    original = await service.create_invitation(
        workspace_id, invitation_command("new@example.com"), actor=actor
    )
    result = await service.resend_invitation(
        workspace_id, original.invitation.id, actor=second_owner
    )
    assert result.invitation.invited_by_user_id == second_owner.sub
    assert result.invitation.email == "new@example.com"
    assert await session.scalar(select(func.count()).select_from(User)) == 2


@pytest.mark.parametrize(
    "state",
    [
        "marketer",
        "analyst",
        "missing_member",
        "invited",
        "suspended",
        "removed",
        "closed",
        "archived",
        "missing_workspace",
        "wrong_workspace",
        "missing_invitation",
    ],
)
@pytest.mark.parametrize("action", ["resend", "revoke"])
async def test_invitation_mutation_requires_owner_and_scoped_access(
    session: AsyncSession, actor: AccessTokenClaims, state: str, action: str
) -> None:
    workspace_id, original, _ = await seed_acceptance(session, actor)
    target_workspace = workspace_id
    target_invitation = original.invitation.id
    if state == "wrong_workspace":
        target_workspace = await seed_workspace(session, user_id=actor.sub)
    elif state == "missing_workspace":
        target_workspace = uuid4()
    elif state == "missing_invitation":
        target_invitation = uuid4()
    else:
        async with session.begin():
            member = await session.scalar(
                select(Membership).where(
                    Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
                )
            )
            workspace = await session.get(Workspace, workspace_id)
            assert member is not None and workspace is not None
            if state in {"marketer", "analyst"}:
                member.role = MembershipRole(state)
            elif state == "missing_member":
                await session.delete(member)
            elif state in {"invited", "suspended", "removed"}:
                member.status = MembershipStatus(state)
                if state == "removed":
                    member.removed_at = datetime.now(UTC)
            elif state == "closed":
                workspace.status = WorkspaceStatus.CLOSED
            elif state == "archived":
                workspace.archived_at = datetime.now(UTC)
    with pytest.raises(
        ForbiddenError if state in {"marketer", "analyst"} else ResourceNotFoundError
    ):
        service = WorkspaceService(session)
        mutate = service.resend_invitation if action == "resend" else service.revoke_invitation
        await mutate(target_workspace, target_invitation, actor=actor)
    assert not session.in_transaction()
    stored = await session.get(WorkspaceInvitation, original.invitation.id)
    assert stored is not None and stored.revoked_at is None
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


@pytest.mark.parametrize("state", ["accepted", "revoked", "future", "active", "suspended"])
async def test_resend_rejects_unusable_invitation_or_recipient(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, original, recipient = await seed_acceptance(session, actor)
    async with session.begin():
        row = await session.get(WorkspaceInvitation, original.invitation.id)
        assert row is not None
        now = datetime.now(UTC)
        if state == "accepted":
            row.accepted_at = now
            row.accepted_by_user_id = recipient.sub
        elif state == "revoked":
            row.revoked_at = now
        elif state == "future":
            row.created_at = now + timedelta(hours=1)
        else:
            session.add(
                Membership(
                    workspace_id=workspace_id,
                    user_id=recipient.sub,
                    role=MembershipRole.ANALYST,
                    status=MembershipStatus(state),
                    accepted_at=now,
                )
            )
    with pytest.raises(InvalidInvitationError if state == "future" else ConflictError):
        await WorkspaceService(session).resend_invitation(
            workspace_id, original.invitation.id, actor=actor
        )
    assert not session.in_transaction()
    stored = await session.get(WorkspaceInvitation, original.invitation.id)
    assert stored is not None
    assert (stored.revoked_at is not None) == (state == "revoked")
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


@pytest.mark.parametrize(
    "state", ["pending", "suspended", "disabled", "unverified", "version", "revoked"]
)
@pytest.mark.parametrize("action", ["resend", "revoke", "list"])
async def test_invitation_operation_rejects_invalid_actor(
    session: AsyncSession, actor: AccessTokenClaims, state: str, action: str
) -> None:
    workspace_id, original, _ = await seed_acceptance(session, actor)
    async with session.begin():
        user = await session.get(User, actor.sub)
        login = await session.get(UserSession, actor.sid)
        assert user is not None and login is not None
        if state == "unverified":
            user.email_verified_at = None
        elif state == "version":
            user.security_version += 1
        elif state == "revoked":
            login.revoked_at = datetime.now(UTC)
        else:
            user.status = UserStatus(state)
    with pytest.raises(AuthenticationRequiredError):
        service = WorkspaceService(session)
        if action == "list":
            await service.list_invitations(workspace_id, actor=actor)
        else:
            mutate = service.resend_invitation if action == "resend" else service.revoke_invitation
            await mutate(workspace_id, original.invitation.id, actor=actor)
    stored = await session.get(WorkspaceInvitation, original.invitation.id)
    assert stored is not None and stored.revoked_at is None
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


@pytest.mark.parametrize("expires", ["jwt", "session"])
@pytest.mark.parametrize("action", ["resend", "revoke", "list"])
async def test_invitation_operation_rechecks_authentication_after_lock_waits(
    session: AsyncSession,
    actor: AccessTokenClaims,
    monkeypatch: pytest.MonkeyPatch,
    expires: str,
    action: str,
) -> None:
    workspace_id, original, _ = await seed_acceptance(session, actor)
    now = datetime.now(UTC)
    if expires == "jwt":
        actor = actor.model_copy(update={"exp": int((now + timedelta(seconds=1)).timestamp())})
    else:
        async with session.begin():
            login = await session.get(UserSession, actor.sid)
            assert login is not None
            login.expires_at = now + timedelta(seconds=1)
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [now, now + timedelta(seconds=2)]
    monkeypatch.setattr(workspace_service_module, "datetime", clock)
    with pytest.raises(AuthenticationRequiredError):
        service = WorkspaceService(session)
        if action == "list":
            await service.list_invitations(workspace_id, actor=actor)
        else:
            mutate = service.resend_invitation if action == "resend" else service.revoke_invitation
            await mutate(workspace_id, original.invitation.id, actor=actor)
    assert clock.now.call_count == 2
    stored = await session.get(WorkspaceInvitation, original.invitation.id)
    assert stored is not None and stored.revoked_at is None


@pytest.mark.parametrize("failure_kind", ["duplicate", "other", "claim"])
async def test_resend_failure_rolls_back_old_revocation_and_replacement(
    session: AsyncSession,
    actor: AccessTokenClaims,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    workspace_id, original, _ = await seed_acceptance(session, actor)
    service = WorkspaceService(session)
    if failure_kind == "claim":
        monkeypatch.setattr(service.repository, "revoke_invitation", AsyncMock(return_value=False))
    else:
        failure = constraint_error(
            "23505",
            "uq_workspace_invitations_open_email"
            if failure_kind == "duplicate"
            else "uq_workspace_invitations_token_hash",
        )
        original_add = service.repository.add_invitation

        async def add_then_fail(invitation: WorkspaceInvitation) -> WorkspaceInvitation:
            await original_add(invitation)
            raise failure

        monkeypatch.setattr(service.repository, "add_invitation", add_then_fail)
    expected = (
        InvitationAlreadyPendingError
        if failure_kind == "duplicate"
        else IntegrityError
        if failure_kind == "other"
        else ConflictError
    )
    with pytest.raises(expected):
        await service.resend_invitation(workspace_id, original.invitation.id, actor=actor)
    assert not session.in_transaction()
    session.expunge_all()
    stored = await session.get(WorkspaceInvitation, original.invitation.id)
    assert stored is not None and stored.revoked_at is None
    assert stored.token_hash == hash_token(original.token.get_secret_value())
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


@pytest.mark.parametrize("state", ["removed", "invited"])
async def test_resend_preserves_inactive_recipient_membership(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, original, recipient = await seed_acceptance(session, actor)
    async with session.begin():
        membership = Membership(
            workspace_id=workspace_id,
            user_id=recipient.sub,
            role=MembershipRole.ANALYST,
            status=MembershipStatus(state),
            removed_at=datetime.now(UTC) if state == "removed" else None,
        )
        session.add(membership)
        await session.flush()
        membership_id = membership.id
    await WorkspaceService(session).resend_invitation(
        workspace_id, original.invitation.id, actor=actor
    )
    session.expunge_all()
    stored = await session.get(Membership, membership_id)
    assert stored is not None and stored.status == MembershipStatus(state)
    assert stored.role == MembershipRole.ANALYST
    assert stored.version == 1
    assert await session.scalar(select(func.count()).select_from(Membership)) == 2


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize(
    "status", [WorkspaceStatus.ACTIVE, WorkspaceStatus.PENDING, WorkspaceStatus.SENDING_SUSPENDED]
)
async def test_revocation_is_idempotent_and_blocks_acceptance(
    session: AsyncSession, actor: AccessTokenClaims, expired: bool, status: WorkspaceStatus
) -> None:
    workspace_id, original, recipient = await seed_acceptance(session, actor)
    async with session.begin():
        workspace = await session.get(Workspace, workspace_id)
        invitation = await session.get(WorkspaceInvitation, original.invitation.id)
        assert workspace is not None and invitation is not None
        workspace.status = status
        if expired:
            invitation.created_at = datetime.now(UTC) - timedelta(days=3)
            invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
    service = WorkspaceService(session)
    before = datetime.now(UTC)
    result = await service.revoke_invitation(workspace_id, original.invitation.id, actor=actor)
    after = datetime.now(UTC)
    assert not session.in_transaction()
    assert result.id == original.invitation.id
    assert result.revoked_at is not None
    assert before <= result.revoked_at.replace(tzinfo=UTC) <= after
    assert result.accepted_at is None and result.accepted_by_user_id is None
    assert "token_hash" not in result.model_dump()
    assert original.token.get_secret_value() not in result.model_dump_json()
    repeated = await service.revoke_invitation(workspace_id, original.invitation.id, actor=actor)
    assert repeated == result
    with pytest.raises(InvalidInvitationError):
        await service.accept_invitation(
            WorkspaceInvitationAcceptRequest(token=original.token), actor=recipient
        )
    with pytest.raises(ConflictError):
        await service.resend_invitation(workspace_id, original.invitation.id, actor=actor)
    assert await session.scalar(select(func.count()).select_from(Membership)) == 1
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


async def test_revocation_cannot_undo_accepted_membership(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id, original, recipient = await seed_acceptance(session, actor)
    service = WorkspaceService(session)
    membership = await service.accept_invitation(
        WorkspaceInvitationAcceptRequest(token=original.token), actor=recipient
    )
    with pytest.raises(ConflictError) as caught:
        await service.revoke_invitation(workspace_id, original.invitation.id, actor=actor)
    assert caught.value.code == "WORKSPACE_INVITATION_ACCEPTED"
    assert not session.in_transaction()
    row = await session.get(WorkspaceInvitation, original.invitation.id)
    member = await session.get(Membership, membership.id)
    assert row is not None and row.revoked_at is None and row.accepted_at is not None
    assert member is not None and member.status == MembershipStatus.ACTIVE
    assert member.version == membership.version


async def test_other_owner_can_revoke_only_the_selected_invitation(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id, original, other_owner = await seed_acceptance(session, actor)
    async with session.begin():
        session.add(
            Membership(
                workspace_id=workspace_id,
                user_id=other_owner.sub,
                role=MembershipRole.OWNER,
                status=MembershipStatus.ACTIVE,
                accepted_at=datetime.now(UTC),
            )
        )
    service = WorkspaceService(session)
    untouched = await service.create_invitation(
        workspace_id, invitation_command("untouched@example.com"), actor=actor
    )
    result = await service.revoke_invitation(
        workspace_id, original.invitation.id, actor=other_owner
    )
    assert result.revoked_at is not None
    assert result.invited_by_user_id == actor.sub
    row = await session.get(WorkspaceInvitation, untouched.invitation.id)
    assert row is not None and row.revoked_at is None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 2


@pytest.mark.parametrize("failure", ["claim", "refresh"])
async def test_revocation_failure_leaves_invitation_usable(
    session: AsyncSession,
    actor: AccessTokenClaims,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    workspace_id, original, recipient = await seed_acceptance(session, actor)
    service = WorkspaceService(session)
    with monkeypatch.context() as patch:
        if failure == "claim":
            patch.setattr(service.repository, "revoke_invitation", AsyncMock(return_value=False))
        else:
            patch.setattr(session, "refresh", AsyncMock(side_effect=RuntimeError("Load failed")))
        with pytest.raises(ConflictError if failure == "claim" else RuntimeError):
            await service.revoke_invitation(workspace_id, original.invitation.id, actor=actor)
    assert not session.in_transaction()
    session.expunge_all()
    async with session.begin():
        row = await session.get(WorkspaceInvitation, original.invitation.id)
        assert row is not None and row.revoked_at is None
    result = await service.accept_invitation(
        WorkspaceInvitationAcceptRequest(token=original.token), actor=recipient
    )
    assert result.status == MembershipStatus.ACTIVE


@pytest.mark.parametrize("state", ["demoted", "revoked_session"])
async def test_repeated_revocation_still_requires_current_authorization(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, original, _ = await seed_acceptance(session, actor)
    service = WorkspaceService(session)
    result = await service.revoke_invitation(workspace_id, original.invitation.id, actor=actor)
    async with session.begin():
        if state == "demoted":
            member = await session.scalar(
                select(Membership).where(
                    Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
                )
            )
            assert member is not None
            member.role = MembershipRole.ANALYST
        else:
            login = await session.get(UserSession, actor.sid)
            assert login is not None
            login.revoked_at = datetime.now(UTC)
    with pytest.raises(ForbiddenError if state == "demoted" else AuthenticationRequiredError):
        await service.revoke_invitation(workspace_id, original.invitation.id, actor=actor)
    row = await session.get(WorkspaceInvitation, original.invitation.id)
    assert row is not None and row.revoked_at == result.revoked_at


async def test_revocation_rejects_future_dated_invitation(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id, original, _ = await seed_acceptance(session, actor)
    async with session.begin():
        row = await session.get(WorkspaceInvitation, original.invitation.id)
        assert row is not None
        row.created_at = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(InvalidInvitationError):
        await WorkspaceService(session).revoke_invitation(
            workspace_id, original.invitation.id, actor=actor
        )
    row = await session.get(WorkspaceInvitation, original.invitation.id)
    assert row is not None and row.revoked_at is None


@pytest.mark.parametrize(
    "status", [WorkspaceStatus.ACTIVE, WorkspaceStatus.PENDING, WorkspaceStatus.SENDING_SUSPENDED]
)
async def test_owner_can_list_empty_invitation_history(
    session: AsyncSession, actor: AccessTokenClaims, status: WorkspaceStatus
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub, status=status)
    assert await WorkspaceService(session).list_invitations(workspace_id, actor=actor) == []
    assert not session.in_transaction()


@pytest.mark.parametrize(
    "state",
    [
        "marketer",
        "analyst",
        "missing",
        "nonmember",
        "invited",
        "suspended",
        "removed",
        "closed",
        "archived",
    ],
)
async def test_invitation_history_is_owner_only(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    workspace_id = uuid4()
    if state != "missing":
        workspace_id = await seed_workspace(
            session,
            user_id=None if state == "nonmember" else actor.sub,
            role=MembershipRole(state)
            if state in {"marketer", "analyst"}
            else MembershipRole.OWNER,
            membership_status=MembershipStatus(state)
            if state in {"invited", "suspended", "removed"}
            else MembershipStatus.ACTIVE,
            status=WorkspaceStatus.CLOSED if state == "closed" else WorkspaceStatus.ACTIVE,
            archived=state == "archived",
        )
    service = WorkspaceService(session)
    listing = AsyncMock()
    monkeypatch.setattr(service.repository, "list_invitations", listing)
    with pytest.raises(
        ForbiddenError if state in {"marketer", "analyst"} else ResourceNotFoundError
    ):
        await service.list_invitations(workspace_id, actor=actor)
    listing.assert_not_awaited()
    assert not session.in_transaction()


async def test_invitation_history_paginates_all_states_without_credentials_or_cross_workspace_rows(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    other_workspace = await seed_workspace(session, user_id=actor.sub)
    now = datetime.now(UTC)
    created = now - timedelta(days=3)
    ids = [UUID(int=value) for value in (40, 10, 30, 20)]
    tokens: list[str] = []
    async with session.begin():
        for invitation_id, state in zip(
            ids, ("pending", "accepted", "revoked", "expired"), strict=True
        ):
            token = f"private-{state}-token"
            tokens.append(token)
            session.add(
                WorkspaceInvitation(
                    id=invitation_id,
                    workspace_id=workspace_id,
                    invited_by_user_id=actor.sub,
                    email=f"{state}@example.com",
                    normalized_email=f"{state}@example.com",
                    role=MembershipRole.ANALYST,
                    token_hash=hash_token(token),
                    created_at=created,
                    updated_at=created,
                    expires_at=now - timedelta(days=1)
                    if state == "expired"
                    else now + timedelta(days=1),
                    accepted_at=now if state == "accepted" else None,
                    accepted_by_user_id=actor.sub if state == "accepted" else None,
                    revoked_at=now if state == "revoked" else None,
                )
            )
        session.add(
            WorkspaceInvitation(
                id=UUID(int=15),
                workspace_id=other_workspace,
                invited_by_user_id=actor.sub,
                email="private@elsewhere.com",
                normalized_email="private@elsewhere.com",
                role=MembershipRole.MARKETER,
                token_hash=hash_token("other-workspace-token"),
                created_at=created,
                updated_at=created,
                expires_at=now + timedelta(days=1),
            )
        )
    service = WorkspaceService(session)
    first = await service.list_invitations(workspace_id, actor=actor, limit=2)
    second = await service.list_invitations(
        workspace_id, actor=actor, limit=2, after=(first[-1].created_at, first[-1].id)
    )
    assert [row.id for row in first + second] == sorted(ids)
    assert (
        await service.list_invitations(
            workspace_id, actor=actor, after=(second[-1].created_at, second[-1].id)
        )
        == []
    )
    # A cursor copied from another workspace cannot broaden authorization scope.
    cross_cursor = await service.list_invitations(
        workspace_id, actor=actor, after=(created, UUID(int=15))
    )
    assert [row.id for row in cross_cursor] == [UUID(int=value) for value in (20, 30, 40)]
    assert not session.in_transaction()
    for row in first + second:
        assert row.workspace_id == workspace_id
        assert "token_hash" not in row.model_dump() and "token" not in row.model_dump()
        for token in tokens:
            assert token not in row.model_dump_json()
            assert hash_token(token) not in row.model_dump_json()
        stored = await session.get(WorkspaceInvitation, row.id)
        assert stored is not None
        assert stored.accepted_at == row.accepted_at
        assert stored.revoked_at == row.revoked_at
        assert stored.updated_at == created.replace(tzinfo=None)


@pytest.mark.parametrize("limit", [None, 1, 100])
async def test_invitation_listing_default_and_maximum_page_sizes(
    session: AsyncSession, actor: AccessTokenClaims, limit: int | None
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    now = datetime.now(UTC)
    async with session.begin():
        session.add_all(
            [
                WorkspaceInvitation(
                    workspace_id=workspace_id,
                    invited_by_user_id=actor.sub,
                    email=f"person{index}@example.com",
                    normalized_email=f"person{index}@example.com",
                    role=MembershipRole.MARKETER,
                    token_hash=hash_token(f"list-token-{index}"),
                    created_at=now,
                    updated_at=now,
                    expires_at=now + timedelta(hours=48),
                )
                for index in range(101)
            ]
        )
    service = WorkspaceService(session)
    result = (
        await service.list_invitations(workspace_id, actor=actor)
        if limit is None
        else await service.list_invitations(workspace_id, actor=actor, limit=limit)
    )
    assert len(result) == (50 if limit is None else limit)
    assert not session.in_transaction()


@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_invitation_listing_rejects_out_of_bounds_page_size(
    session: AsyncSession, actor: AccessTokenClaims, limit: int
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    with pytest.raises(ValueError, match="between 1 and 100"):
        await WorkspaceService(session).list_invitations(workspace_id, actor=actor, limit=limit)
    assert not session.in_transaction()


async def test_invitation_listing_failure_releases_transaction(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, original, _ = await seed_acceptance(session, actor)
    service = WorkspaceService(session)
    with monkeypatch.context() as patch:
        patch.setattr(
            service.repository,
            "list_invitations",
            AsyncMock(side_effect=RuntimeError("Read failed")),
        )
        with pytest.raises(RuntimeError, match="Read failed"):
            await service.list_invitations(workspace_id, actor=actor)
    assert not session.in_transaction()
    result = await service.list_invitations(workspace_id, actor=actor)
    assert [row.id for row in result] == [original.invitation.id]
    assert result[0].accepted_at is None and result[0].revoked_at is None


async def seed_role_target(
    session: AsyncSession,
    actor: AccessTokenClaims,
) -> tuple[UUID, UUID, AccessTokenClaims]:
    workspace_id, invitation, recipient = await seed_acceptance(session, actor)
    membership = await WorkspaceService(session).accept_invitation(
        WorkspaceInvitationAcceptRequest(token=invitation.token), actor=recipient
    )
    return workspace_id, membership.id, recipient


@pytest.mark.parametrize("old_role", list(MembershipRole))
@pytest.mark.parametrize("new_role", list(MembershipRole))
async def test_owner_changes_roles_with_versioning_and_noop_support(
    session: AsyncSession,
    actor: AccessTokenClaims,
    old_role: MembershipRole,
    new_role: MembershipRole,
) -> None:
    workspace_id, membership_id, recipient = await seed_role_target(session, actor)
    async with session.begin():
        membership = await session.get(Membership, membership_id)
        assert membership is not None
        membership.role = old_role
        await session.flush()
        version = membership.version
        accepted_at = membership.accepted_at
    result = await WorkspaceService(session).update_membership_role(
        workspace_id,
        membership_id,
        MembershipRoleUpdateRequest(role=new_role, expected_version=version),
        actor=actor,
    )
    assert not session.in_transaction()
    assert result.role == new_role
    assert result.version == version + int(old_role != new_role)
    assert result.status == MembershipStatus.ACTIVE
    assert result.user_id == recipient.sub
    assert result.accepted_at == accepted_at
    assert result.removed_at is None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 2


@pytest.mark.parametrize(
    "state", ["absent", "disabled", "unverified", "suspended_membership", "other_workspace"]
)
async def test_demotion_requires_another_eligible_owner(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    async with session.begin():
        target = await session.get(Membership, member_id)
        user = await session.get(User, recipient.sub)
        owner = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
            )
        )
        assert target is not None and user is not None and owner is not None
        owner_id, version = owner.id, owner.version
        if state != "absent":
            target.role = MembershipRole.OWNER
        if state == "disabled":
            user.status = UserStatus.DISABLED
        elif state == "unverified":
            user.email_verified_at = None
        elif state == "suspended_membership":
            target.status = MembershipStatus.SUSPENDED
        elif state == "other_workspace":
            other = Workspace(name="Other")
            session.add(other)
            await session.flush()
            target.workspace_id = other.id
    with pytest.raises(LastWorkspaceOwnerError):
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            owner_id,
            MembershipRoleUpdateRequest(role=MembershipRole.ANALYST, expected_version=version),
            actor=actor,
        )
    assert not session.in_transaction()
    stored = await session.get(Membership, owner_id)
    assert stored is not None and stored.role == MembershipRole.OWNER and stored.version == version


async def test_self_demotion_with_second_owner_revokes_invites_permanently(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id, member_id, second_owner = await seed_role_target(session, actor)
    service = WorkspaceService(session)
    await service.update_membership_role(
        workspace_id,
        member_id,
        MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
        actor=actor,
    )
    open_invite = await service.create_invitation(
        workspace_id, invitation_command("new@example.com"), actor=actor
    )
    expired_id = await seed_expired_invitation(
        session, workspace_id=workspace_id, inviter_id=actor.sub
    )
    other_invite = await service.create_invitation(
        workspace_id, invitation_command("other@example.com"), actor=second_owner
    )
    async with session.begin():
        owner = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
            )
        )
        assert owner is not None
        owner_id, version = owner.id, owner.version
    demoted = await service.update_membership_role(
        workspace_id,
        owner_id,
        MembershipRoleUpdateRequest(role=MembershipRole.MARKETER, expected_version=version),
        actor=actor,
    )
    assert demoted.role == MembershipRole.MARKETER
    with pytest.raises(ForbiddenError):
        await service.list_invitations(workspace_id, actor=actor)
    await service.update_membership_role(
        workspace_id,
        owner_id,
        MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=demoted.version),
        actor=second_owner,
    )
    for invitation_id in (open_invite.invitation.id, expired_id):
        row = await session.get(WorkspaceInvitation, invitation_id)
        assert row is not None and row.revoked_at is not None and row.accepted_at is None
    untouched = await session.get(WorkspaceInvitation, other_invite.invitation.id)
    assert untouched is not None and untouched.revoked_at is None
    accepted = await session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.accepted_at.is_not(None),
        )
    )
    assert accepted is not None and accepted.revoked_at is None


@pytest.mark.parametrize("state", ["pending", "suspended", "disabled", "unverified"])
async def test_owner_promotion_requires_eligible_user(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    async with session.begin():
        user = await session.get(User, recipient.sub)
        assert user is not None
        if state == "unverified":
            user.email_verified_at = None
        else:
            user.status = UserStatus(state)
    with pytest.raises(ConflictError) as caught:
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
            actor=actor,
        )
    assert caught.value.code == "WORKSPACE_OWNER_INELIGIBLE"
    row = await session.get(Membership, member_id)
    assert row is not None and row.role == MembershipRole.MARKETER and row.version == 1


@pytest.mark.parametrize("state", ["invited", "suspended", "removed"])
async def test_role_changes_cannot_reactivate_membership(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    async with session.begin():
        row = await session.get(Membership, member_id)
        assert row is not None
        row.status = MembershipStatus(state)
        if state == "removed":
            row.removed_at = datetime.now(UTC)
        await session.flush()
        version = row.version
    with pytest.raises(ConflictError) as caught:
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=version),
            actor=actor,
        )
    assert caught.value.code == "WORKSPACE_MEMBERSHIP_INACTIVE"
    row = await session.get(Membership, member_id)
    assert row is not None and row.status == MembershipStatus(state) and row.version == version


@pytest.mark.parametrize("role", [MembershipRole.MARKETER, MembershipRole.ANALYST])
async def test_nonowner_cannot_promote_themselves(
    session: AsyncSession, actor: AccessTokenClaims, role: MembershipRole
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    async with session.begin():
        row = await session.get(Membership, member_id)
        assert row is not None
        row.role = role
        await session.flush()
        version = row.version
    with pytest.raises(ForbiddenError):
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=version),
            actor=recipient,
        )


@pytest.mark.parametrize(
    "scope", ["workspace", "membership", "cross_workspace", "closed", "archived"]
)
async def test_role_update_requires_accessible_workspace_and_scoped_membership(
    session: AsyncSession, actor: AccessTokenClaims, scope: str
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    if scope == "workspace":
        workspace_id = uuid4()
    elif scope == "membership":
        member_id = uuid4()
    elif scope == "cross_workspace":
        workspace_id = await seed_workspace(session, user_id=actor.sub)
    else:
        async with session.begin():
            workspace = await session.get(Workspace, workspace_id)
            assert workspace is not None
            if scope == "closed":
                workspace.status = WorkspaceStatus.CLOSED
            else:
                workspace.archived_at = datetime.now(UTC)
    with pytest.raises(ResourceNotFoundError):
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
            actor=actor,
        )
    assert not session.in_transaction()


@pytest.mark.parametrize("noop", [False, True])
async def test_role_update_rejects_stale_version_even_for_noop(
    session: AsyncSession, actor: AccessTokenClaims, noop: bool
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    with pytest.raises(MembershipVersionConflictError):
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(
                role=MembershipRole.MARKETER if noop else MembershipRole.OWNER, expected_version=99
            ),
            actor=actor,
        )


@pytest.mark.parametrize("failure", ["stale", "revocation"])
async def test_role_update_failure_rolls_back_demotion_and_invitation_revocation(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    service = WorkspaceService(session)
    await service.update_membership_role(
        workspace_id,
        member_id,
        MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
        actor=actor,
    )
    invitation = await service.create_invitation(
        workspace_id, invitation_command("rollback@example.com"), actor=recipient
    )
    if failure == "stale":
        original_flush = service.repository.flush

        async def fail_flush() -> None:
            await original_flush()
            raise StaleDataError("Simulated version conflict")

        monkeypatch.setattr(service.repository, "flush", fail_flush)
    else:
        original_revoke = service.repository.revoke_invitations_by_inviter

        async def fail_revoke(
            *, workspace_id: UUID, invited_by_user_id: UUID, now: datetime
        ) -> None:
            await original_revoke(
                workspace_id=workspace_id, invited_by_user_id=invited_by_user_id, now=now
            )
            raise RuntimeError("Revocation failed")

        monkeypatch.setattr(service.repository, "revoke_invitations_by_inviter", fail_revoke)
    with pytest.raises(MembershipVersionConflictError if failure == "stale" else RuntimeError):
        await service.update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.ANALYST, expected_version=2),
            actor=actor,
        )
    assert not session.in_transaction()
    session.expunge_all()
    member = await session.get(Membership, member_id)
    invite = await session.get(WorkspaceInvitation, invitation.invitation.id)
    assert member is not None and member.role == MembershipRole.OWNER and member.version == 2
    assert invite is not None and invite.revoked_at is None


@pytest.mark.parametrize(
    "state", ["pending", "suspended", "disabled", "unverified", "version", "revoked"]
)
async def test_role_update_rejects_invalid_actor(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    async with session.begin():
        user = await session.get(User, actor.sub)
        login = await session.get(UserSession, actor.sid)
        assert user is not None and login is not None
        if state == "unverified":
            user.email_verified_at = None
        elif state == "version":
            user.security_version += 1
        elif state == "revoked":
            login.revoked_at = datetime.now(UTC)
        else:
            user.status = UserStatus(state)
    with pytest.raises(AuthenticationRequiredError):
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
            actor=actor,
        )
    assert not session.in_transaction()
    member = await session.get(Membership, member_id)
    assert member is not None and member.role == MembershipRole.MARKETER and member.version == 1


@pytest.mark.parametrize("state", ["missing", "invited", "suspended", "removed"])
async def test_role_update_requires_active_owner_membership(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    async with session.begin():
        owner = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
            )
        )
        assert owner is not None
        if state == "missing":
            await session.delete(owner)
        else:
            owner.status = MembershipStatus(state)
            if state == "removed":
                owner.removed_at = datetime.now(UTC)
    with pytest.raises(ResourceNotFoundError):
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
            actor=actor,
        )


@pytest.mark.parametrize("expires", ["jwt", "session"])
async def test_role_update_rechecks_expiry_after_lock_waits(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch, expires: str
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    now = datetime.now(UTC)
    if expires == "jwt":
        actor = actor.model_copy(update={"exp": int((now + timedelta(seconds=1)).timestamp())})
    else:
        async with session.begin():
            login = await session.get(UserSession, actor.sid)
            assert login is not None
            login.expires_at = now + timedelta(seconds=1)
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [now, now + timedelta(seconds=2)]
    monkeypatch.setattr(workspace_service_module, "datetime", clock)
    with pytest.raises(AuthenticationRequiredError):
        await WorkspaceService(session).update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
            actor=actor,
        )
    assert clock.now.call_count == 2
    assert not session.in_transaction()
    member = await session.get(Membership, member_id)
    assert member is not None and member.role == MembershipRole.MARKETER and member.version == 1


async def test_role_update_locks_users_in_uuid_order_before_workspace(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    service = WorkspaceService(session)
    events: list[tuple[str, UUID]] = []
    user_lock = service.identity_repository.lock_user
    workspace_lock = service.repository.lock_workspace

    async def lock_user(user_id: UUID) -> User | None:
        events.append(("user", user_id))
        return await user_lock(user_id)

    async def lock_workspace(workspace_id: UUID) -> Workspace | None:
        events.append(("workspace", workspace_id))
        return await workspace_lock(workspace_id)

    monkeypatch.setattr(service.identity_repository, "lock_user", lock_user)
    monkeypatch.setattr(service.repository, "lock_workspace", lock_workspace)
    await service.update_membership_role(
        workspace_id,
        member_id,
        MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
        actor=actor,
    )
    assert events[:2] == [("user", user_id) for user_id in sorted({actor.sub, recipient.sub})]
    assert events[2:] == [("user", actor.sub), ("workspace", workspace_id)]


async def test_only_owner_can_keep_their_current_role_without_version_change(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    async with session.begin():
        owner = await session.scalar(
            select(Membership).where(Membership.workspace_id == workspace_id)
        )
        assert owner is not None
        member_id, version = owner.id, owner.version
    result = await WorkspaceService(session).update_membership_role(
        workspace_id,
        member_id,
        MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=version),
        actor=actor,
    )
    assert result.role == MembershipRole.OWNER and result.version == version


async def test_owner_can_demote_disabled_coowner_without_counting_them_as_eligible(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    async with session.begin():
        member = await session.get(Membership, member_id)
        user = await session.get(User, recipient.sub)
        assert member is not None and user is not None
        member.role = MembershipRole.OWNER
        user.status = UserStatus.DISABLED
        await session.flush()
        version = member.version
    result = await WorkspaceService(session).update_membership_role(
        workspace_id,
        member_id,
        MembershipRoleUpdateRequest(role=MembershipRole.ANALYST, expected_version=version),
        actor=actor,
    )
    assert result.role == MembershipRole.ANALYST
    assert result.version == version + 1


async def lifecycle_action(
    service: WorkspaceService,
    action: str,
    workspace_id: UUID,
    membership_id: UUID,
    version: int,
    actor: AccessTokenClaims,
) -> MembershipResponse:
    command = MembershipVersionRequest(expected_version=version)
    if action == "leave":
        return await service.leave_workspace(workspace_id, command, actor=actor)
    methods = {
        "suspend": service.suspend_membership,
        "restore": service.restore_membership,
        "remove": service.remove_membership,
    }
    return await methods[action](workspace_id, membership_id, command, actor=actor)


@pytest.mark.parametrize("action", ["suspend", "restore", "remove"])
@pytest.mark.parametrize("initial", list(MembershipStatus))
async def test_membership_lifecycle_transition_matrix(
    session: AsyncSession, actor: AccessTokenClaims, action: str, initial: MembershipStatus
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    async with session.begin():
        member = await session.get(Membership, member_id)
        assert member is not None
        member.status = initial
        member.removed_at = datetime.now(UTC) if initial == MembershipStatus.REMOVED else None
        await session.flush()
        version, accepted_at = member.version, member.accepted_at
    destinations = {
        "suspend": MembershipStatus.SUSPENDED,
        "restore": MembershipStatus.ACTIVE,
        "remove": MembershipStatus.REMOVED,
    }
    allowed = {
        "suspend": {MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED},
        "restore": {MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED},
        "remove": set(MembershipStatus),
    }
    service = WorkspaceService(session)
    if initial not in allowed[action]:
        with pytest.raises(ConflictError) as caught:
            await lifecycle_action(service, action, workspace_id, member_id, version, actor)
        assert caught.value.code == "WORKSPACE_MEMBERSHIP_INVALID_TRANSITION"
        row = await session.get(Membership, member_id)
        assert row is not None and row.status == initial and row.version == version
        return
    result = await lifecycle_action(service, action, workspace_id, member_id, version, actor)
    assert not session.in_transaction()
    assert result.status == destinations[action]
    assert result.role == MembershipRole.MARKETER
    assert result.accepted_at == accepted_at
    assert result.version == version + int(initial != destinations[action])
    assert (result.removed_at is not None) == (action == "remove")
    repeat = await lifecycle_action(service, action, workspace_id, member_id, result.version, actor)
    assert repeat == result
    with pytest.raises(MembershipVersionConflictError):
        await lifecycle_action(service, action, workspace_id, member_id, 999, actor)


@pytest.mark.parametrize("initial", list(MembershipStatus))
async def test_leave_is_self_scoped_and_available_to_suspended_members(
    session: AsyncSession, actor: AccessTokenClaims, initial: MembershipStatus
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    async with session.begin():
        member = await session.get(Membership, member_id)
        assert member is not None
        member.status = initial
        member.removed_at = datetime.now(UTC) if initial == MembershipStatus.REMOVED else None
        await session.flush()
        version = member.version
    service = WorkspaceService(session)
    if initial in {MembershipStatus.INVITED, MembershipStatus.REMOVED}:
        with pytest.raises(ResourceNotFoundError):
            await lifecycle_action(service, "leave", workspace_id, member_id, version, recipient)
    else:
        result = await lifecycle_action(
            service, "leave", workspace_id, member_id, version, recipient
        )
        assert result.user_id == recipient.sub and result.status == MembershipStatus.REMOVED
        with pytest.raises(ResourceNotFoundError):
            await service.list_memberships(workspace_id, actor=recipient)
        with pytest.raises(ResourceNotFoundError):
            await lifecycle_action(
                service, "leave", workspace_id, member_id, result.version, recipient
            )
    owner = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
        )
    )
    assert (
        owner is not None
        and owner.status == MembershipStatus.ACTIVE
        and owner.role == MembershipRole.OWNER
    )


@pytest.mark.parametrize("action", ["suspend", "remove", "leave"])
@pytest.mark.parametrize("coowner", [False, True])
async def test_lifecycle_preserves_last_eligible_owner_and_permanently_revokes_sender_invites(
    session: AsyncSession, actor: AccessTokenClaims, action: str, coowner: bool
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    service = WorkspaceService(session)
    if coowner:
        await service.update_membership_role(
            workspace_id,
            member_id,
            MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
            actor=actor,
        )
    invitation = await service.create_invitation(
        workspace_id, invitation_command("outgoing@example.com"), actor=actor
    )
    async with session.begin():
        owner = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
            )
        )
        assert owner is not None
        owner_id, version = owner.id, owner.version
    if not coowner:
        with pytest.raises(LastWorkspaceOwnerError):
            await lifecycle_action(service, action, workspace_id, owner_id, version, actor)
        row = await session.get(WorkspaceInvitation, invitation.invitation.id)
        assert row is not None and row.revoked_at is None
    else:
        result = await lifecycle_action(service, action, workspace_id, owner_id, version, actor)
        if action == "suspend":
            restored = await lifecycle_action(
                service, "restore", workspace_id, owner_id, result.version, recipient
            )
            assert (
                restored.role == MembershipRole.OWNER and restored.status == MembershipStatus.ACTIVE
            )
        row = await session.get(WorkspaceInvitation, invitation.invitation.id)
        assert row is not None and row.revoked_at is not None


@pytest.mark.parametrize("action", ["remove", "leave"])
async def test_removal_invalidates_recipient_links_and_requires_fresh_invitation(
    session: AsyncSession, actor: AccessTokenClaims, action: str
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    now = datetime.now(UTC)
    async with session.begin():
        # Simulate an outstanding historical link, not a new invitation to an active member.
        session.add(
            WorkspaceInvitation(
                workspace_id=workspace_id,
                invited_by_user_id=actor.sub,
                email="teammate@example.com",
                normalized_email="teammate@example.com",
                role=MembershipRole.ANALYST,
                token_hash=hash_token("stale-recipient-link"),
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(hours=48),
            )
        )
    service = WorkspaceService(session)
    removed = await lifecycle_action(
        service, action, workspace_id, member_id, 1, recipient if action == "leave" else actor
    )
    with pytest.raises(InvalidInvitationError):
        await service.accept_invitation(
            WorkspaceInvitationAcceptRequest(token=SecretStr("stale-recipient-link")),
            actor=recipient,
        )
    with pytest.raises(ConflictError):
        await lifecycle_action(service, "restore", workspace_id, member_id, removed.version, actor)
    new_invitation = await service.create_invitation(
        workspace_id, invitation_command(role="analyst"), actor=actor
    )
    joined = await service.accept_invitation(
        WorkspaceInvitationAcceptRequest(token=new_invitation.token), actor=recipient
    )
    assert joined.id == member_id and joined.status == MembershipStatus.ACTIVE
    assert joined.role == MembershipRole.ANALYST and joined.removed_at is None


@pytest.mark.parametrize(
    "state", ["pending", "suspended", "disabled", "unverified", "missing_acceptance"]
)
async def test_restoration_does_not_bypass_account_state_or_acceptance(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    service = WorkspaceService(session)
    suspended = await lifecycle_action(service, "suspend", workspace_id, member_id, 1, actor)
    async with session.begin():
        user = await session.get(User, recipient.sub)
        member = await session.get(Membership, member_id)
        assert user is not None and member is not None
        if state == "unverified":
            user.email_verified_at = None
        elif state == "missing_acceptance":
            member.accepted_at = None
        else:
            user.status = UserStatus(state)
        await session.flush()
        version = member.version
    with pytest.raises(ConflictError):
        await lifecycle_action(service, "restore", workspace_id, member_id, version, actor)
    member = await session.get(Membership, member_id)
    assert member is not None and member.status == MembershipStatus.SUSPENDED
    assert member.version >= suspended.version


@pytest.mark.parametrize("action", ["suspend", "restore", "remove"])
@pytest.mark.parametrize("role", [MembershipRole.MARKETER, MembershipRole.ANALYST])
async def test_member_cannot_manage_their_own_status_as_nonowner(
    session: AsyncSession, actor: AccessTokenClaims, action: str, role: MembershipRole
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    async with session.begin():
        row = await session.get(Membership, member_id)
        assert row is not None
        row.role = role
        await session.flush()
        version = row.version
    with pytest.raises(ForbiddenError):
        await lifecycle_action(
            WorkspaceService(session), action, workspace_id, member_id, version, recipient
        )


@pytest.mark.parametrize("action", ["suspend", "restore", "remove", "leave"])
@pytest.mark.parametrize("expires", ["jwt", "session"])
async def test_lifecycle_rechecks_authentication_after_lock_waits(
    session: AsyncSession,
    actor: AccessTokenClaims,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    expires: str,
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    now = datetime.now(UTC)
    if expires == "jwt":
        actor = actor.model_copy(update={"exp": int((now + timedelta(seconds=1)).timestamp())})
    else:
        async with session.begin():
            login = await session.get(UserSession, actor.sid)
            assert login is not None
            login.expires_at = now + timedelta(seconds=1)
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [now, now + timedelta(seconds=2)]
    monkeypatch.setattr(workspace_service_module, "datetime", clock)
    with pytest.raises(AuthenticationRequiredError):
        await lifecycle_action(WorkspaceService(session), action, workspace_id, member_id, 1, actor)
    assert clock.now.call_count == 2 and not session.in_transaction()


@pytest.mark.parametrize("action", ["suspend", "restore", "remove"])
@pytest.mark.parametrize("failure", ["stale", "refresh"])
async def test_lifecycle_failure_rolls_back_status_and_revocations(
    session: AsyncSession,
    actor: AccessTokenClaims,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    failure: str,
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    service = WorkspaceService(session)
    promoted = await service.update_membership_role(
        workspace_id,
        member_id,
        MembershipRoleUpdateRequest(role=MembershipRole.OWNER, expected_version=1),
        actor=actor,
    )
    if action == "restore":
        promoted = await lifecycle_action(
            service, "suspend", workspace_id, member_id, promoted.version, actor
        )
    else:
        await service.create_invitation(
            workspace_id, invitation_command("rollback@example.com"), actor=recipient
        )
    original_flush = service.repository.flush

    async def fail_flush() -> None:
        await original_flush()
        raise StaleDataError("Simulated write conflict")

    if failure == "stale":
        monkeypatch.setattr(service.repository, "flush", fail_flush)
    else:
        monkeypatch.setattr(session, "refresh", AsyncMock(side_effect=RuntimeError("Read failed")))
    with pytest.raises(MembershipVersionConflictError if failure == "stale" else RuntimeError):
        await lifecycle_action(service, action, workspace_id, member_id, promoted.version, actor)
    assert not session.in_transaction()
    session.expunge_all()
    row = await session.get(Membership, member_id)
    assert row is not None and row.status == promoted.status and row.version == promoted.version
    invitations = (
        await session.scalars(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.invited_by_user_id == recipient.sub
            )
        )
    ).all()
    assert all(invitation.revoked_at is None for invitation in invitations)


@pytest.mark.parametrize("viewer", ["owner", "marketer", "analyst"])
async def test_membership_directory_privacy_and_history_access(
    session: AsyncSession, actor: AccessTokenClaims, viewer: str
) -> None:
    workspace_id, member_id, recipient = await seed_role_target(session, actor)
    inactive_ids = []
    async with session.begin():
        member = await session.get(Membership, member_id)
        assert member is not None
        if viewer == "analyst":
            member.role = MembershipRole.ANALYST
        for state in (
            MembershipStatus.INVITED,
            MembershipStatus.SUSPENDED,
            MembershipStatus.REMOVED,
        ):
            email = f"{state.value}@example.com"
            user = User(email=email, normalized_email=email, password_hash="never-expose")
            session.add(user)
            await session.flush()
            row = Membership(
                workspace_id=workspace_id,
                user_id=user.id,
                role=MembershipRole.ANALYST,
                status=state,
                removed_at=datetime.now(UTC) if state == MembershipStatus.REMOVED else None,
            )
            session.add(row)
            await session.flush()
            inactive_ids.append(row.id)
    service = WorkspaceService(session)
    claims = actor if viewer == "owner" else recipient
    rows = await service.list_memberships(workspace_id, actor=claims)
    assert len(rows) == (5 if viewer == "owner" else 2)
    assert not session.in_transaction()
    for entry in rows:
        assert isinstance(entry, OwnerMembershipResponse) == (viewer == "owner")
        assert ("email" in entry.model_dump()) == (viewer == "owner")
        assert (
            "password_hash" not in entry.model_dump()
            and "normalized_email" not in entry.model_dump()
        )
        detail = await service.get_membership(workspace_id, entry.id, actor=claims)
        assert detail == entry
    if viewer != "owner":
        with pytest.raises(ForbiddenError):
            await service.list_memberships(
                workspace_id, actor=claims, status=MembershipStatus.REMOVED
            )
        for row_id in inactive_ids:
            with pytest.raises(ResourceNotFoundError):
                await service.get_membership(workspace_id, row_id, actor=claims)
    else:
        removed = await service.list_memberships(
            workspace_id, actor=claims, status=MembershipStatus.REMOVED
        )
        assert len(removed) == 1 and removed[0].id == inactive_ids[-1]


@pytest.mark.parametrize("limit", [None, 1, 100, 0, -1, 101])
async def test_membership_listing_page_limits(
    session: AsyncSession, actor: AccessTokenClaims, limit: int | None
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    async with session.begin():
        now = datetime.now(UTC)
        for index in range(100):
            email = f"page{index}@example.com"
            user = User(email=email, normalized_email=email, password_hash="test")
            session.add(user)
            await session.flush()
            session.add(
                Membership(
                    workspace_id=workspace_id,
                    user_id=user.id,
                    role=MembershipRole.MARKETER,
                    status=MembershipStatus.ACTIVE,
                    accepted_at=now,
                )
            )
    service = WorkspaceService(session)
    if limit is not None and not 1 <= limit <= 100:
        with pytest.raises(ValueError):
            await service.list_memberships(workspace_id, actor=actor, limit=limit)
    else:
        rows = (
            await service.list_memberships(workspace_id, actor=actor)
            if limit is None
            else await service.list_memberships(workspace_id, actor=actor, limit=limit)
        )
        assert len(rows) == (50 if limit is None else limit)
    assert not session.in_transaction()


async def test_membership_cursor_filters_are_stable_and_workspace_scoped(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    other_workspace = await seed_workspace(session, user_id=actor.sub)
    now = datetime.now(UTC)
    async with session.begin():
        members = (
            await session.scalars(select(Membership).where(Membership.workspace_id == workspace_id))
        ).all()
        for row in members:
            row.created_at = now
        expected = sorted(row.id for row in members)
        other = await session.scalar(
            select(Membership).where(Membership.workspace_id == other_workspace)
        )
        assert other is not None
        other_id = other.id
    service = WorkspaceService(session)
    first = await service.list_memberships(workspace_id, actor=actor, limit=1)
    second = await service.list_memberships(
        workspace_id, actor=actor, limit=1, after=(first[0].created_at, first[0].id)
    )
    assert [row.id for row in first + second] == expected
    assert (
        await service.list_memberships(
            workspace_id, actor=actor, after=(second[0].created_at, second[0].id)
        )
        == []
    )
    marketers = await service.list_memberships(
        workspace_id, actor=actor, role=MembershipRole.MARKETER
    )
    assert [row.id for row in marketers] == [member_id]
    with pytest.raises(ResourceNotFoundError):
        await service.get_membership(workspace_id, other_id, actor=actor)
    boundary = await service.list_memberships(workspace_id, actor=actor, after=(now, other_id))
    assert all(row.workspace_id == workspace_id for row in boundary)


@pytest.mark.parametrize("action", ["suspend", "restore", "remove", "leave", "list", "get"])
@pytest.mark.parametrize("blocked", ["closed", "archived", "revoked_session"])
async def test_membership_operations_reject_unavailable_workspace_or_identity(
    session: AsyncSession, actor: AccessTokenClaims, action: str, blocked: str
) -> None:
    workspace_id, member_id, _ = await seed_role_target(session, actor)
    async with session.begin():
        workspace = await session.get(Workspace, workspace_id)
        login = await session.get(UserSession, actor.sid)
        assert workspace is not None and login is not None
        if blocked == "closed":
            workspace.status = WorkspaceStatus.CLOSED
        elif blocked == "archived":
            workspace.archived_at = datetime.now(UTC)
        else:
            login.revoked_at = datetime.now(UTC)
    service = WorkspaceService(session)
    with pytest.raises(
        AuthenticationRequiredError if blocked == "revoked_session" else ResourceNotFoundError
    ):
        if action == "list":
            await service.list_memberships(workspace_id, actor=actor)
        elif action == "get":
            await service.get_membership(workspace_id, member_id, actor=actor)
        else:
            await lifecycle_action(service, action, workspace_id, member_id, 1, actor)
    assert not session.in_transaction()


@pytest.mark.parametrize("action", ["suspend", "restore", "remove"])
async def test_membership_mutations_reject_cross_workspace_targets(
    session: AsyncSession, actor: AccessTokenClaims, action: str
) -> None:
    _, member_id, _ = await seed_role_target(session, actor)
    other_workspace = await seed_workspace(session, user_id=actor.sub)
    with pytest.raises(ResourceNotFoundError):
        await lifecycle_action(
            WorkspaceService(session), action, other_workspace, member_id, 1, actor
        )


async def seed_acceptance(
    session: AsyncSession, owner: AccessTokenClaims, *, role: str = "marketer"
) -> tuple[UUID, InvitationCreationResult, AccessTokenClaims]:
    workspace_id = await seed_workspace(session, user_id=owner.sub)
    invitation = await WorkspaceService(session).create_invitation(
        workspace_id, invitation_command(role=role), actor=owner
    )
    now = datetime.now(UTC)
    async with session.begin():
        recipient = User(
            email="Teammate@example.com",
            normalized_email="teammate@example.com",
            password_hash="test",
            status=UserStatus.ACTIVE,
            email_verified_at=now,
        )
        session.add(recipient)
        await session.flush()
        login = UserSession(
            user_id=recipient.id,
            security_version=recipient.security_version,
            refresh_token_hash=hash_token(uuid4().hex),
            issued_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=1),
        )
        session.add(login)
        await session.flush()
        tokens = AccessTokenService(secret_key=SecretStr("workspace-tests-only-signing-key-123456"))
        claims = tokens.decode(
            tokens.create(
                user_id=recipient.id,
                session_id=login.id,
                security_version=recipient.security_version,
            )
        )
    return workspace_id, invitation, claims


@pytest.mark.parametrize("role", ["marketer", "analyst"])
async def test_acceptance_creates_membership_and_rejects_replay(
    session: AsyncSession, actor: AccessTokenClaims, role: str
) -> None:
    workspace_id, invitation, recipient = await seed_acceptance(session, actor, role=role)
    service = WorkspaceService(session)
    command = WorkspaceInvitationAcceptRequest(token=invitation.token)
    result = await service.accept_invitation(command, actor=recipient)
    assert not session.in_transaction()
    assert result.workspace_id == workspace_id
    assert result.user_id == recipient.sub
    assert result.role == MembershipRole(role)
    assert result.status == MembershipStatus.ACTIVE
    assert result.invited_by_user_id == actor.sub
    assert result.accepted_at is not None
    assert result.removed_at is None
    with pytest.raises(InvalidInvitationError):
        await service.accept_invitation(command, actor=recipient)
    assert not session.in_transaction()
    session.expunge_all()
    stored = await session.get(WorkspaceInvitation, invitation.invitation.id)
    assert stored is not None
    assert stored.accepted_by_user_id == recipient.sub
    assert stored.accepted_at == result.accepted_at
    assert stored.revoked_at is None
    assert await session.get(Membership, result.id) is not None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 2


@pytest.mark.parametrize("state", ["removed", "invited", "active", "suspended"])
async def test_acceptance_respects_existing_membership_state(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, invitation, recipient = await seed_acceptance(session, actor, role="analyst")
    async with session.begin():
        membership = Membership(
            workspace_id=workspace_id,
            user_id=recipient.sub,
            role=MembershipRole.OWNER,
            status=MembershipStatus(state),
            accepted_at=None if state == "invited" else datetime.now(UTC),
            removed_at=datetime.now(UTC) if state == "removed" else None,
        )
        session.add(membership)
        await session.flush()
        membership_id = membership.id
    command = WorkspaceInvitationAcceptRequest(token=invitation.token)
    if state in {"active", "suspended"}:
        with pytest.raises(ConflictError) as caught:
            await WorkspaceService(session).accept_invitation(command, actor=recipient)
        assert caught.value.code == (
            "WORKSPACE_MEMBER_ALREADY_ACTIVE" if state == "active" else "WORKSPACE_MEMBER_SUSPENDED"
        )
    else:
        result = await WorkspaceService(session).accept_invitation(command, actor=recipient)
        assert result.id == membership_id
        assert result.version == 2
        assert result.role == MembershipRole.ANALYST
        assert result.status == MembershipStatus.ACTIVE
        assert result.removed_at is None
        assert result.accepted_at is not None
    assert not session.in_transaction()
    stored_invitation = await session.get(WorkspaceInvitation, invitation.invitation.id)
    stored_member = await session.get(Membership, membership_id)
    assert stored_invitation is not None and stored_member is not None
    if state in {"active", "suspended"}:
        assert stored_invitation.accepted_at is None
        assert stored_member.status == MembershipStatus(state)
        assert stored_member.role == MembershipRole.OWNER
        assert stored_member.version == 1
    assert await session.scalar(select(func.count()).select_from(Membership)) == 2


@pytest.mark.parametrize(
    "state",
    [
        "pending",
        "suspended",
        "disabled",
        "unverified",
        "version",
        "revoked",
        "expired_session",
        "expired_jwt",
        "wrong_email",
    ],
)
async def test_acceptance_requires_matching_verified_current_identity(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    _, invitation, recipient = await seed_acceptance(session, actor)
    if state == "expired_jwt":
        recipient = recipient.model_copy(update={"exp": int(datetime.now(UTC).timestamp()) - 1})
    else:
        async with session.begin():
            if state in {"revoked", "expired_session"}:
                login = await session.get(UserSession, recipient.sid)
                assert login is not None
                if state == "revoked":
                    login.revoked_at = datetime.now(UTC)
                else:
                    login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                user = await session.get(User, recipient.sub)
                assert user is not None
                if state == "unverified":
                    user.email_verified_at = None
                elif state == "version":
                    user.security_version += 1
                elif state == "wrong_email":
                    user.email = user.normalized_email = "different@example.com"
                else:
                    user.status = UserStatus(state)
    expected_error = (
        InvalidInvitationError if state == "wrong_email" else AuthenticationRequiredError
    )
    with pytest.raises(expected_error):
        await WorkspaceService(session).accept_invitation(
            WorkspaceInvitationAcceptRequest(token=invitation.token), actor=recipient
        )
    assert not session.in_transaction()
    stored = await session.get(WorkspaceInvitation, invitation.invitation.id)
    assert stored is not None
    assert stored.accepted_at is None
    assert stored.revoked_at is None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 1


@pytest.mark.parametrize("state", ["unknown", "expired", "future", "revoked", "closed", "archived"])
async def test_acceptance_rejects_unusable_invitation_or_workspace(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, invitation, recipient = await seed_acceptance(session, actor)
    token = invitation.token
    if state == "unknown":
        token = SecretStr("unknown-token")
    else:
        async with session.begin():
            if state in {"closed", "archived"}:
                workspace = await session.get(Workspace, workspace_id)
                assert workspace is not None
                if state == "closed":
                    workspace.status = WorkspaceStatus.CLOSED
                else:
                    workspace.archived_at = datetime.now(UTC)
            else:
                row = await session.get(WorkspaceInvitation, invitation.invitation.id)
                assert row is not None
                now = datetime.now(UTC)
                if state == "expired":
                    row.created_at = now - timedelta(days=3)
                    row.expires_at = now - timedelta(days=1)
                elif state == "future":
                    row.created_at = now + timedelta(days=1)
                    row.expires_at = now + timedelta(days=3)
                else:
                    row.revoked_at = now
    with pytest.raises(InvalidInvitationError):
        await WorkspaceService(session).accept_invitation(
            WorkspaceInvitationAcceptRequest(token=token), actor=recipient
        )
    assert not session.in_transaction()
    row = await session.get(WorkspaceInvitation, invitation.invitation.id)
    assert row is not None and row.accepted_at is None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 1


@pytest.mark.parametrize(
    "state",
    ["demoted", "suspended", "removed", "missing", "disabled_account", "unverified_account"],
)
async def test_acceptance_permanently_revokes_when_sender_loses_eligibility(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id, invitation, recipient = await seed_acceptance(session, actor)
    async with session.begin():
        owner_member = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
            )
        )
        owner = await session.get(User, actor.sub)
        assert owner_member is not None and owner is not None
        if state == "demoted":
            owner_member.role = MembershipRole.MARKETER
        elif state in {"suspended", "removed"}:
            owner_member.status = MembershipStatus(state)
            if state == "removed":
                owner_member.removed_at = datetime.now(UTC)
        elif state == "missing":
            await session.delete(owner_member)
        elif state == "disabled_account":
            owner.status = UserStatus.DISABLED
        else:
            owner.email_verified_at = None
    service = WorkspaceService(session)
    command = WorkspaceInvitationAcceptRequest(token=invitation.token)
    with pytest.raises(InvalidInvitationError):
        await service.accept_invitation(command, actor=recipient)
    assert not session.in_transaction()
    async with session.begin():
        stored = await session.get(WorkspaceInvitation, invitation.invitation.id)
        assert stored is not None and stored.revoked_at is not None
        assert stored.accepted_at is None
        revoked_at = stored.revoked_at
        owner = await session.get(User, actor.sub)
        assert owner is not None
        owner.status = UserStatus.ACTIVE
        owner.email_verified_at = datetime.now(UTC)
        owner_member = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == actor.sub
            )
        )
        if owner_member is None:
            owner_member = Membership(workspace_id=workspace_id, user_id=actor.sub)
            session.add(owner_member)
        owner_member.role = MembershipRole.OWNER
        owner_member.status = MembershipStatus.ACTIVE
        owner_member.accepted_at = datetime.now(UTC)
        owner_member.removed_at = None
    with pytest.raises(InvalidInvitationError):
        await service.accept_invitation(command, actor=recipient)
    stored = await session.get(WorkspaceInvitation, invitation.invitation.id)
    assert stored is not None
    assert stored.revoked_at == revoked_at
    assert stored.accepted_at is None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 1


@pytest.mark.parametrize("existing", [False, True])
async def test_membership_failure_rolls_back_invitation_consumption(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    workspace_id, invitation, recipient = await seed_acceptance(session, actor)
    if existing:
        async with session.begin():
            session.add(
                Membership(
                    workspace_id=workspace_id,
                    user_id=recipient.sub,
                    role=MembershipRole.OWNER,
                    status=MembershipStatus.REMOVED,
                    removed_at=datetime.now(UTC),
                )
            )
    service = WorkspaceService(session)
    if existing:
        original_flush = service.repository.flush

        async def flush_then_fail() -> None:
            await original_flush()
            raise StaleDataError("Simulated concurrent modification")

        monkeypatch.setattr(service.repository, "flush", flush_then_fail)
    else:
        # Response refresh happens after membership insertion has been flushed.
        monkeypatch.setattr(
            service.session, "refresh", AsyncMock(side_effect=RuntimeError("Response load failed"))
        )
    with pytest.raises(ConflictError if existing else RuntimeError):
        await service.accept_invitation(
            WorkspaceInvitationAcceptRequest(token=invitation.token), actor=recipient
        )
    assert not session.in_transaction()
    session.expunge_all()
    stored = await session.get(WorkspaceInvitation, invitation.invitation.id)
    assert stored is not None and stored.accepted_at is None and stored.accepted_by_user_id is None
    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == recipient.sub
        )
    )
    if existing:
        assert membership is not None
        assert membership.status == MembershipStatus.REMOVED
        assert membership.role == MembershipRole.OWNER
        assert membership.version == 1
    else:
        assert membership is None


@pytest.mark.parametrize("expires", ["jwt", "session", "invitation"])
async def test_acceptance_rechecks_expiry_after_locks(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch, expires: str
) -> None:
    _, invitation, recipient = await seed_acceptance(session, actor)
    now = datetime.now(UTC)
    if expires == "jwt":
        recipient = recipient.model_copy(
            update={"exp": int((now + timedelta(seconds=1)).timestamp())}
        )
    else:
        async with session.begin():
            if expires == "session":
                login = await session.get(UserSession, recipient.sid)
                assert login is not None
                login.expires_at = now + timedelta(seconds=1)
            else:
                row = await session.get(WorkspaceInvitation, invitation.invitation.id)
                assert row is not None
                row.expires_at = now + timedelta(seconds=1)
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [now, now + timedelta(seconds=2)]
    monkeypatch.setattr(workspace_service_module, "datetime", clock)
    with pytest.raises(
        InvalidInvitationError if expires == "invitation" else AuthenticationRequiredError
    ):
        await WorkspaceService(session).accept_invitation(
            WorkspaceInvitationAcceptRequest(token=invitation.token), actor=recipient
        )
    assert clock.now.call_count == 2
    assert not session.in_transaction()
    stored = await session.get(WorkspaceInvitation, invitation.invitation.id)
    assert stored is not None and stored.accepted_at is None
    assert await session.scalar(select(func.count()).select_from(Membership)) == 1


async def test_acceptance_locks_both_users_in_uuid_order_before_workspace(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, invitation, recipient = await seed_acceptance(session, actor)
    service = WorkspaceService(session)
    events: list[tuple[str, UUID]] = []
    original_user_lock = service.identity_repository.lock_user
    original_workspace_lock = service.repository.lock_workspace

    async def lock_user(user_id: UUID) -> User | None:
        events.append(("user", user_id))
        return await original_user_lock(user_id)

    async def lock_workspace(workspace_id: UUID) -> Workspace | None:
        events.append(("workspace", workspace_id))
        return await original_workspace_lock(workspace_id)

    monkeypatch.setattr(service.identity_repository, "lock_user", lock_user)
    monkeypatch.setattr(service.repository, "lock_workspace", lock_workspace)
    await service.accept_invitation(
        WorkspaceInvitationAcceptRequest(token=invitation.token), actor=recipient
    )
    assert events[:2] == [("user", user_id) for user_id in sorted({actor.sub, recipient.sub})]
    assert events[2] == ("user", recipient.sub)  # Recheck an already-held lock.
    assert events[3] == ("workspace", invitation.invitation.workspace_id)


async def seed_expired_invitation(
    session: AsyncSession, *, workspace_id: UUID, inviter_id: UUID
) -> UUID:
    expires_at = datetime.now(UTC) - timedelta(minutes=1)
    async with session.begin():
        invitation = WorkspaceInvitation(
            workspace_id=workspace_id,
            invited_by_user_id=inviter_id,
            email="teammate@example.com",
            normalized_email="teammate@example.com",
            role=MembershipRole.MARKETER,
            token_hash=hash_token("old-token"),
            created_at=expires_at - timedelta(hours=48),
            updated_at=expires_at - timedelta(hours=48),
            expires_at=expires_at,
        )
        session.add(invitation)
        await session.flush()
        return invitation.id


@pytest.mark.parametrize("role", ["marketer", "analyst"])
async def test_invitation_creation_commits_hash_and_48_hour_expiry_without_membership(
    session: AsyncSession, actor: AccessTokenClaims, role: str
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    before = datetime.now(UTC)
    result = await WorkspaceService(session).create_invitation(
        workspace_id, invitation_command(" Teammate@EXAMPLE.COM ", role), actor=actor
    )
    after = datetime.now(UTC)
    assert isinstance(result, InvitationCreationResult)
    assert not session.in_transaction()
    assert result.invitation.workspace_id == workspace_id
    assert result.invitation.invited_by_user_id == actor.sub
    assert result.invitation.role == MembershipRole(role)
    assert before <= result.invitation.created_at <= after
    assert result.invitation.expires_at - result.invitation.created_at == timedelta(hours=48)
    assert result.invitation.accepted_at is None
    assert result.invitation.revoked_at is None
    raw_token = result.token.get_secret_value()
    assert raw_token
    assert raw_token not in repr(result)
    assert "token" not in result.invitation.model_dump()
    assert "token_hash" not in result.invitation.model_dump()
    session.expunge_all()
    stored = await session.get(WorkspaceInvitation, result.invitation.id)
    assert stored is not None
    assert stored.email == "Teammate@example.com"
    assert stored.normalized_email == "teammate@example.com"
    assert stored.token_hash == hash_token(raw_token)
    assert stored.token_hash != raw_token
    assert await session.scalar(select(func.count()).select_from(Membership)) == 1
    assert await session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("role", [MembershipRole.MARKETER, MembershipRole.ANALYST])
async def test_invitation_creation_requires_owner_role(
    session: AsyncSession, actor: AccessTokenClaims, role: MembershipRole
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub, role=role)
    with pytest.raises(ForbiddenError) as caught:
        await WorkspaceService(session).create_invitation(
            workspace_id, invitation_command(), actor=actor
        )
    assert caught.value.status_code == 403
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 0


@pytest.mark.parametrize(
    "state", ["missing", "nonmember", "invited", "suspended", "removed", "closed", "archived"]
)
async def test_invitation_creation_hides_inaccessible_workspaces(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id = uuid4()
    if state != "missing":
        workspace_id = await seed_workspace(
            session,
            user_id=None if state == "nonmember" else actor.sub,
            membership_status=MembershipStatus(state)
            if state in {"invited", "suspended", "removed"}
            else MembershipStatus.ACTIVE,
            status=WorkspaceStatus.CLOSED if state == "closed" else WorkspaceStatus.ACTIVE,
            archived=state == "archived",
        )
    with pytest.raises(ResourceNotFoundError):
        await WorkspaceService(session).create_invitation(
            workspace_id, invitation_command(), actor=actor
        )
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 0


@pytest.mark.parametrize("state", ["nonmember", "removed", "active", "suspended"])
async def test_recipient_membership_controls_invitation_eligibility(
    session: AsyncSession, actor: AccessTokenClaims, state: str
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    membership_id = None
    async with session.begin():
        recipient = User(
            email="teammate@example.com",
            normalized_email="teammate@example.com",
            password_hash="test",
        )
        session.add(recipient)
        await session.flush()
        if state != "nonmember":
            membership = Membership(
                workspace_id=workspace_id,
                user_id=recipient.id,
                role=MembershipRole.ANALYST,
                status=MembershipStatus(state),
                accepted_at=datetime.now(UTC),
                removed_at=datetime.now(UTC) if state == "removed" else None,
            )
            session.add(membership)
            await session.flush()
            membership_id = membership.id
    service = WorkspaceService(session)
    if state in {"active", "suspended"}:
        with pytest.raises(ConflictError) as caught:
            await service.create_invitation(
                workspace_id, invitation_command("TEAMMATE@example.com"), actor=actor
            )
        assert caught.value.code == (
            "WORKSPACE_MEMBER_ALREADY_ACTIVE" if state == "active" else "WORKSPACE_MEMBER_SUSPENDED"
        )
    else:
        result = await service.create_invitation(workspace_id, invitation_command(), actor=actor)
        assert result.invitation.accepted_at is None
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == (
        0 if state in {"active", "suspended"} else 1
    )
    assert await session.scalar(select(func.count()).select_from(Membership)) == (
        1 if state == "nonmember" else 2
    )
    if membership_id is not None:
        stored = await session.get(Membership, membership_id)
        assert stored is not None
        assert stored.status == MembershipStatus(state)
        assert stored.role == MembershipRole.ANALYST
        assert stored.version == 1


async def test_duplicate_invitation_is_case_insensitive_and_preserves_original(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    service = WorkspaceService(session)
    first = await service.create_invitation(workspace_id, invitation_command(), actor=actor)
    with pytest.raises(InvitationAlreadyPendingError) as caught:
        await service.create_invitation(
            workspace_id, invitation_command("TEAMMATE@example.com", "analyst"), actor=actor
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "WORKSPACE_INVITATION_PENDING"
    assert not session.in_transaction()
    stored = await session.get(WorkspaceInvitation, first.invitation.id)
    assert stored is not None
    assert stored.revoked_at is None
    assert stored.role == MembershipRole.MARKETER
    assert stored.token_hash == hash_token(first.token.get_secret_value())
    assert stored.expires_at.replace(tzinfo=UTC) == first.invitation.expires_at
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


async def test_same_email_can_be_invited_to_two_workspaces(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    first_workspace = await seed_workspace(session, user_id=actor.sub)
    second_workspace = await seed_workspace(session, user_id=actor.sub)
    service = WorkspaceService(session)
    first = await service.create_invitation(first_workspace, invitation_command(), actor=actor)
    second = await service.create_invitation(second_workspace, invitation_command(), actor=actor)
    assert first.invitation.id != second.invitation.id
    assert first.token.get_secret_value() != second.token.get_secret_value()
    assert second.invitation.workspace_id == second_workspace


async def test_expired_invitation_is_revoked_and_replaced(
    session: AsyncSession, actor: AccessTokenClaims
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    old_id = await seed_expired_invitation(session, workspace_id=workspace_id, inviter_id=actor.sub)
    result = await WorkspaceService(session).create_invitation(
        workspace_id, invitation_command(), actor=actor
    )
    assert not session.in_transaction()
    assert result.invitation.id != old_id
    assert result.invitation.expires_at - result.invitation.created_at == timedelta(hours=48)
    old = await session.get(WorkspaceInvitation, old_id)
    assert old is not None
    assert old.revoked_at is not None
    assert old.revoked_at.replace(tzinfo=UTC) == result.invitation.created_at
    assert old.token_hash == hash_token("old-token")
    assert old.token_hash != hash_token(result.token.get_secret_value())
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 2


@pytest.mark.parametrize("duplicate", [True, False])
async def test_invitation_creation_failure_rolls_back_expired_revocation_and_insert(
    session: AsyncSession,
    actor: AccessTokenClaims,
    monkeypatch: pytest.MonkeyPatch,
    duplicate: bool,
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    old_id = await seed_expired_invitation(session, workspace_id=workspace_id, inviter_id=actor.sub)
    service = WorkspaceService(session)
    failure = constraint_error(
        "23505",
        "uq_workspace_invitations_open_email"
        if duplicate
        else "uq_workspace_invitations_token_hash",
    )
    original_add = service.repository.add_invitation

    async def add_then_fail(invitation: WorkspaceInvitation) -> WorkspaceInvitation:
        await original_add(invitation)
        raise failure

    monkeypatch.setattr(service.repository, "add_invitation", add_then_fail)
    expected_error = InvitationAlreadyPendingError if duplicate else IntegrityError
    with pytest.raises(expected_error) as caught:
        await service.create_invitation(workspace_id, invitation_command(), actor=actor)
    if not duplicate:
        assert caught.value is failure
    assert not session.in_transaction()
    session.expunge_all()
    old = await session.get(WorkspaceInvitation, old_id)
    assert old is not None
    assert old.revoked_at is None
    assert old.token_hash == hash_token("old-token")
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


@pytest.mark.parametrize(
    "sqlstate,name,expected",
    [
        ("23505", "uq_workspace_invitations_open_email", True),
        ("23505", "uq_workspace_invitations_token_hash", False),
        ("23514", "uq_workspace_invitations_open_email", False),
    ],
)
def test_invitation_constraint_classifier_is_specific(
    sqlstate: str, name: str, expected: bool
) -> None:
    assert _is_duplicate_invitation_error(constraint_error(sqlstate, name)) is expected


@pytest.mark.parametrize("state", ["expired_jwt", "expired_session"])
async def test_invitation_creation_rechecks_authentication_after_lock_waits(
    session: AsyncSession, actor: AccessTokenClaims, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    workspace_id = await seed_workspace(session, user_id=actor.sub)
    now = datetime.now(UTC)
    if state == "expired_jwt":
        actor = actor.model_copy(update={"exp": int((now + timedelta(seconds=1)).timestamp())})
    else:
        async with session.begin():
            login = await session.get(UserSession, actor.sid)
            assert login is not None
            login.expires_at = now + timedelta(seconds=1)
    # Advance only the service clock between initial authentication and
    # the post-lock check; no real sleeps or concurrent PostgreSQL locks.
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [now, now + timedelta(seconds=2)]
    monkeypatch.setattr(workspace_service_module, "datetime", clock)
    with pytest.raises(AuthenticationRequiredError):
        await WorkspaceService(session).create_invitation(
            workspace_id, invitation_command(), actor=actor
        )
    assert clock.now.call_count == 2
    assert not session.in_transaction()
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 0
