"""Invitation repository behavior on SQLite, not PostgreSQL concurrency tests."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database import Base
from app.core.security import hash_token
from app.modules.identity.models import User
from app.modules.workspace.model import MembershipRole, Workspace, WorkspaceInvitation
from app.modules.workspace.repository import WorkspaceRepository

NOW = datetime(2026, 1, 10, 12, tzinfo=UTC)


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


@dataclass(frozen=True)
class InvitationSeed:
    invitation: WorkspaceInvitation
    recipient_id: UUID


def make_invitation(
    *, workspace_id: UUID, inviter_id: UUID, email: str = "recipient@example.com"
) -> WorkspaceInvitation:
    return WorkspaceInvitation(
        workspace_id=workspace_id,
        invited_by_user_id=inviter_id,
        email=email,
        normalized_email=email,
        role=MembershipRole.MARKETER,
        token_hash=hash_token(uuid4().hex),
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=47),
    )


@pytest.fixture
async def seed(session: AsyncSession) -> InvitationSeed:
    async with session.begin():
        sender = User(
            email="owner@example.com", normalized_email="owner@example.com", password_hash="test"
        )
        recipient = User(
            email="recipient@example.com",
            normalized_email="recipient@example.com",
            password_hash="test",
        )
        workspace = Workspace(name="Invitation test")
        session.add_all([sender, recipient, workspace])
        await session.flush()
        invitation = make_invitation(workspace_id=workspace.id, inviter_id=sender.id)
        session.add(invitation)
        await session.flush()
        return InvitationSeed(invitation=invitation, recipient_id=recipient.id)


async def consume(repository: WorkspaceRepository, seed: InvitationSeed) -> bool:
    invitation = seed.invitation
    return await repository.consume_invitation(
        workspace_id=invitation.workspace_id,
        invitation_id=invitation.id,
        token_hash=invitation.token_hash,
        normalized_email=invitation.normalized_email,
        accepted_by_user_id=seed.recipient_id,
        now=NOW,
    )


async def test_add_invitation_flushes_without_committing(
    session: AsyncSession, seed: InvitationSeed
) -> None:
    invitation = make_invitation(
        workspace_id=seed.invitation.workspace_id,
        inviter_id=seed.invitation.invited_by_user_id,
        email="another@example.com",
    )
    result = await WorkspaceRepository(session).add_invitation(invitation)
    assert result is invitation
    assert invitation.id is not None
    invitation_id = invitation.id
    assert session.in_transaction()
    await session.rollback()
    assert await session.get(WorkspaceInvitation, invitation_id) is None
    assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1


@pytest.mark.parametrize("state", ["open", "expired", "accepted", "revoked"])
async def test_hash_lookup_includes_terminal_rows_but_open_lookup_does_not(
    session: AsyncSession, seed: InvitationSeed, state: str
) -> None:
    invitation = seed.invitation
    if state == "expired":
        invitation.expires_at = NOW
    elif state == "accepted":
        invitation.accepted_at = NOW
        invitation.accepted_by_user_id = seed.recipient_id
    elif state == "revoked":
        invitation.revoked_at = NOW
    await session.flush()
    repository = WorkspaceRepository(session)
    assert (
        await repository.get_invitation_by_token_hash(token_hash=invitation.token_hash)
        is invitation
    )
    assert await repository.get_invitation_by_token_hash(token_hash=hash_token("unknown")) is None
    result = await repository.get_open_invitation_for_email(
        workspace_id=invitation.workspace_id, normalized_email=invitation.normalized_email
    )
    assert result is (invitation if state in {"open", "expired"} else None)
    assert (
        await repository.get_open_invitation_for_email(
            workspace_id=uuid4(), normalized_email=invitation.normalized_email
        )
        is None
    )
    assert (
        await repository.get_open_invitation_for_email(
            workspace_id=invitation.workspace_id, normalized_email="wrong@example.com"
        )
        is None
    )


async def test_lock_is_scoped_and_refreshes_loaded_values(
    session: AsyncSession, seed: InvitationSeed
) -> None:
    invitation = seed.invitation
    repository = WorkspaceRepository(session)
    await session.execute(
        update(WorkspaceInvitation)
        .where(WorkspaceInvitation.id == invitation.id)
        .values(role=MembershipRole.ANALYST)
        .execution_options(synchronize_session=False)
    )
    assert invitation.role == MembershipRole.MARKETER
    locked = await repository.lock_invitation(
        workspace_id=invitation.workspace_id, invitation_id=invitation.id
    )
    assert locked is not None
    assert locked is invitation
    assert locked.role == MembershipRole.ANALYST
    assert (
        await repository.lock_invitation(workspace_id=uuid4(), invitation_id=invitation.id) is None
    )
    assert (
        await repository.lock_invitation(
            workspace_id=invitation.workspace_id, invitation_id=uuid4()
        )
        is None
    )


async def test_consume_is_single_use_and_records_recipient(
    session: AsyncSession, seed: InvitationSeed
) -> None:
    repository = WorkspaceRepository(session)
    invitation = seed.invitation
    invitation_id = invitation.id
    assert await consume(repository, seed) is True
    assert await consume(repository, seed) is False
    assert (
        await repository.revoke_invitation(
            workspace_id=invitation.workspace_id, invitation_id=invitation.id, now=NOW
        )
        is False
    )
    await session.commit()
    session.expunge_all()
    stored = await session.get(WorkspaceInvitation, invitation_id)
    assert stored is not None
    assert stored.accepted_by_user_id == seed.recipient_id
    assert stored.accepted_at is not None
    assert stored.accepted_at.replace(tzinfo=UTC) == NOW
    assert stored.updated_at.replace(tzinfo=UTC) == NOW
    assert stored.revoked_at is None


@pytest.mark.parametrize(
    "failure",
    [
        "workspace",
        "id",
        "hash",
        "email",
        "expired",
        "expiry_boundary",
        "future",
        "accepted",
        "revoked",
    ],
)
async def test_consume_rejects_wrong_credentials_and_unusable_states(
    session: AsyncSession, seed: InvitationSeed, failure: str
) -> None:
    invitation = seed.invitation
    if failure in {"expired", "expiry_boundary"}:
        invitation.expires_at = NOW - timedelta(seconds=1) if failure == "expired" else NOW
    elif failure == "future":
        invitation.created_at = NOW + timedelta(seconds=1)
        invitation.expires_at = invitation.created_at + timedelta(hours=48)
    elif failure == "accepted":
        invitation.accepted_at = NOW - timedelta(minutes=1)
        invitation.accepted_by_user_id = seed.recipient_id
    elif failure == "revoked":
        invitation.revoked_at = NOW - timedelta(minutes=1)
    await session.flush()
    await session.refresh(invitation)
    before = (
        invitation.accepted_at,
        invitation.accepted_by_user_id,
        invitation.revoked_at,
        invitation.updated_at,
    )
    result = await WorkspaceRepository(session).consume_invitation(
        workspace_id=uuid4() if failure == "workspace" else invitation.workspace_id,
        invitation_id=uuid4() if failure == "id" else invitation.id,
        token_hash=hash_token("wrong") if failure == "hash" else invitation.token_hash,
        normalized_email="wrong@example.com" if failure == "email" else invitation.normalized_email,
        accepted_by_user_id=seed.recipient_id,
        now=NOW,
    )
    assert result is False
    await session.refresh(invitation)
    assert (
        invitation.accepted_at,
        invitation.accepted_by_user_id,
        invitation.revoked_at,
        invitation.updated_at,
    ) == before


@pytest.mark.parametrize("expired", [False, True])
async def test_revoke_includes_expired_rows_and_is_idempotent(
    session: AsyncSession, seed: InvitationSeed, expired: bool
) -> None:
    invitation = seed.invitation
    if expired:
        invitation.expires_at = NOW
        await session.flush()
    repository = WorkspaceRepository(session)
    assert (
        await repository.revoke_invitation(
            workspace_id=invitation.workspace_id, invitation_id=invitation.id, now=NOW
        )
        is True
    )
    assert (
        await repository.revoke_invitation(
            workspace_id=invitation.workspace_id,
            invitation_id=invitation.id,
            now=NOW + timedelta(minutes=1),
        )
        is False
    )
    assert await consume(repository, seed) is False
    await session.refresh(invitation)
    assert invitation.revoked_at is not None
    assert invitation.revoked_at.replace(tzinfo=UTC) == NOW
    assert invitation.accepted_at is None
    assert invitation.accepted_by_user_id is None


@pytest.mark.parametrize("wrong_scope", ["workspace", "invitation"])
async def test_revoke_cannot_change_another_invitation(
    session: AsyncSession, seed: InvitationSeed, wrong_scope: str
) -> None:
    invitation = seed.invitation
    assert (
        await WorkspaceRepository(session).revoke_invitation(
            workspace_id=uuid4() if wrong_scope == "workspace" else invitation.workspace_id,
            invitation_id=uuid4() if wrong_scope == "invitation" else invitation.id,
            now=NOW,
        )
        is False
    )
    await session.refresh(invitation)
    assert invitation.revoked_at is None


async def test_bulk_revocation_is_scoped_and_preserves_terminal_rows(
    session: AsyncSession, seed: InvitationSeed
) -> None:
    original = seed.invitation
    other_sender = User(
        email="other@example.com", normalized_email="other@example.com", password_hash="test"
    )
    other_workspace = Workspace(name="Other workspace")
    session.add_all([other_sender, other_workspace])
    await session.flush()
    rows: dict[str, WorkspaceInvitation] = {}
    for state in ("expired", "accepted", "revoked", "other_sender", "other_workspace"):
        row = make_invitation(
            workspace_id=other_workspace.id
            if state == "other_workspace"
            else original.workspace_id,
            inviter_id=other_sender.id if state == "other_sender" else original.invited_by_user_id,
            email=f"{state}@example.com",
        )
        if state == "expired":
            row.expires_at = NOW
        elif state == "accepted":
            row.accepted_at = NOW - timedelta(minutes=1)
            row.accepted_by_user_id = seed.recipient_id
        elif state == "revoked":
            row.revoked_at = NOW - timedelta(minutes=1)
        rows[state] = row
        session.add(row)
    await session.flush()
    repository = WorkspaceRepository(session)
    for now in (NOW, NOW + timedelta(minutes=1)):
        await repository.revoke_invitations_by_inviter(
            workspace_id=original.workspace_id,
            invited_by_user_id=original.invited_by_user_id,
            now=now,
        )
    for row in [original, *rows.values()]:
        await session.refresh(row)
    for row in (original, rows["expired"]):
        assert row.revoked_at is not None
        assert row.revoked_at.replace(tzinfo=UTC) == NOW
    assert rows["accepted"].revoked_at is None
    assert rows["accepted"].accepted_at is not None
    assert rows["accepted"].accepted_at.replace(tzinfo=UTC) == NOW - timedelta(minutes=1)
    assert rows["revoked"].revoked_at is not None
    assert rows["revoked"].revoked_at.replace(tzinfo=UTC) == NOW - timedelta(minutes=1)
    assert rows["other_sender"].revoked_at is None
    assert rows["other_workspace"].revoked_at is None


@pytest.mark.parametrize("operation", ["consume", "revoke", "bulk_revoke"])
async def test_lifecycle_changes_remain_under_callers_transaction(
    session: AsyncSession, seed: InvitationSeed, operation: str
) -> None:
    invitation = seed.invitation
    invitation_id = invitation.id
    repository = WorkspaceRepository(session)
    if operation == "consume":
        assert await consume(repository, seed) is True
    elif operation == "revoke":
        assert (
            await repository.revoke_invitation(
                workspace_id=invitation.workspace_id, invitation_id=invitation.id, now=NOW
            )
            is True
        )
    else:
        await repository.revoke_invitations_by_inviter(
            workspace_id=invitation.workspace_id,
            invited_by_user_id=invitation.invited_by_user_id,
            now=NOW,
        )
    assert session.in_transaction()
    await session.rollback()
    session.expunge_all()
    stored = await session.get(WorkspaceInvitation, invitation_id)
    assert stored is not None
    assert stored.accepted_at is None
    assert stored.accepted_by_user_id is None
    assert stored.revoked_at is None


async def test_listing_scopes_history_and_paginates_timestamp_ties(
    session: AsyncSession, seed: InvitationSeed
) -> None:
    original = seed.invitation
    rows = [original]
    for state in ("accepted", "revoked", "expired"):
        invitation = make_invitation(
            workspace_id=original.workspace_id,
            inviter_id=original.invited_by_user_id,
            email=f"{state}@example.com",
        )
        if state == "accepted":
            invitation.accepted_at = NOW
            invitation.accepted_by_user_id = seed.recipient_id
        elif state == "revoked":
            invitation.revoked_at = NOW
        else:
            invitation.expires_at = NOW
        session.add(invitation)
        rows.append(invitation)
    other_workspace = Workspace(name="Not visible")
    session.add(other_workspace)
    await session.flush()
    session.add(
        make_invitation(workspace_id=other_workspace.id, inviter_id=original.invited_by_user_id)
    )
    await session.flush()
    repository = WorkspaceRepository(session)
    first = await repository.list_invitations(workspace_id=original.workspace_id, limit=2)
    second = await repository.list_invitations(
        workspace_id=original.workspace_id, limit=2, after=(first[-1].created_at, first[-1].id)
    )
    assert len(first) == len(second) == 2
    assert [row.id for row in first + second] == sorted(row.id for row in rows)
    assert (
        await repository.list_invitations(
            workspace_id=original.workspace_id, after=(second[-1].created_at, second[-1].id)
        )
        == []
    )
    assert await repository.list_invitations(workspace_id=uuid4()) == []


@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_listing_rejects_invalid_limits(session: AsyncSession, limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        await WorkspaceRepository(session).list_invitations(workspace_id=uuid4(), limit=limit)
