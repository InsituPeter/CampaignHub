"""Invitation constraints on SQLite and migration SQL on PostgreSQL's dialect.

These tests do not prove PostgreSQL locking or invitation-service authorization.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from io import StringIO
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.security import hash_token
from app.modules.identity.models import User
from app.modules.workspace.model import MembershipRole, Workspace, WorkspaceInvitation


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()


def seed_invitation(session: Session) -> WorkspaceInvitation:
    sender = User(
        email="owner@example.com", normalized_email="owner@example.com", password_hash="test"
    )
    workspace = Workspace(name="Invitation test")
    session.add_all([sender, workspace])
    session.flush()
    now = datetime.now(UTC)
    invitation = WorkspaceInvitation(
        workspace_id=workspace.id,
        invited_by_user_id=sender.id,
        email="New.Person@example.com",
        normalized_email="new.person@example.com",
        role=MembershipRole.MARKETER,
        token_hash=hash_token(uuid4().hex),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=48),
    )
    session.add(invitation)
    session.flush()
    return invitation


@pytest.mark.parametrize("role", [MembershipRole.MARKETER, MembershipRole.ANALYST])
def test_invitation_can_precede_recipient_registration(
    engine: Engine, role: MembershipRole
) -> None:
    with Session(engine) as session:
        invitation = seed_invitation(session)
        invitation.role = role
        session.flush()
        assert invitation.id is not None
        assert invitation.accepted_at is None
        assert invitation.accepted_by_user_id is None
        assert invitation.revoked_at is None
        assert invitation.expires_at - invitation.created_at == timedelta(hours=48)
        assert (
            session.scalar(select(User).where(User.normalized_email == invitation.normalized_email))
            is None
        )
        assert "token" not in WorkspaceInvitation.__table__.columns


@pytest.mark.parametrize(
    "invalid_state",
    [
        "owner_role",
        "blank_email",
        "untrimmed_email",
        "raw_token",
        "invalid_expiry",
        "early_acceptance",
        "expired_acceptance",
        "early_revocation",
        "accepted_and_revoked",
        "accepted_without_user",
        "user_without_acceptance",
        "missing_sender",
        "missing_workspace",
    ],
)
def test_invalid_invitation_state_is_rejected(engine: Engine, invalid_state: str) -> None:
    with Session(engine) as session:
        invitation = seed_invitation(session)
        created = invitation.created_at
        if invalid_state == "owner_role":
            invitation.role = MembershipRole.OWNER
        elif invalid_state == "blank_email":
            invitation.normalized_email = " "
        elif invalid_state == "untrimmed_email":
            invitation.normalized_email = " person@example.com "
        elif invalid_state == "raw_token":
            invitation.token_hash = "not-a-sha256-digest"
        elif invalid_state == "invalid_expiry":
            invitation.expires_at = created
        elif invalid_state in {"early_acceptance", "expired_acceptance", "accepted_and_revoked"}:
            invitation.accepted_by_user_id = invitation.invited_by_user_id
            if invalid_state == "early_acceptance":
                invitation.accepted_at = created - timedelta(seconds=1)
            elif invalid_state == "expired_acceptance":
                invitation.accepted_at = invitation.expires_at
            else:
                invitation.accepted_at = created
                invitation.revoked_at = created
        elif invalid_state == "early_revocation":
            invitation.revoked_at = created - timedelta(seconds=1)
        elif invalid_state == "accepted_without_user":
            invitation.accepted_at = created
        elif invalid_state == "user_without_acceptance":
            invitation.accepted_by_user_id = invitation.invited_by_user_id
        elif invalid_state == "missing_sender":
            invitation.invited_by_user_id = uuid4()
        else:
            invitation.workspace_id = uuid4()
        with pytest.raises(IntegrityError):
            session.flush()


def replacement_for(invitation: WorkspaceInvitation) -> WorkspaceInvitation:
    now = datetime.now(UTC)
    return WorkspaceInvitation(
        workspace_id=invitation.workspace_id,
        invited_by_user_id=invitation.invited_by_user_id,
        email=invitation.email,
        normalized_email=invitation.normalized_email,
        role=invitation.role,
        token_hash=hash_token(uuid4().hex),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=48),
    )


@pytest.mark.parametrize("expired", [False, True])
def test_open_invitation_blocks_duplicate_even_after_expiry(engine: Engine, expired: bool) -> None:
    with Session(engine) as session:
        invitation = seed_invitation(session)
        if expired:
            invitation.created_at = datetime.now(UTC) - timedelta(days=3)
            invitation.expires_at = invitation.created_at + timedelta(hours=48)
            session.flush()
        session.add(replacement_for(invitation))
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("terminal_state", ["revoked", "accepted"])
def test_terminal_invitation_allows_new_invitation(engine: Engine, terminal_state: str) -> None:
    with Session(engine) as session:
        invitation = seed_invitation(session)
        if terminal_state == "revoked":
            invitation.revoked_at = datetime.now(UTC)
        else:
            recipient = User(
                email=invitation.email,
                normalized_email=invitation.normalized_email,
                password_hash="test",
            )
            session.add(recipient)
            session.flush()
            invitation.accepted_at = datetime.now(UTC)
            invitation.accepted_by_user_id = recipient.id
        session.flush()
        replacement = replacement_for(invitation)
        session.add(replacement)
        session.flush()
        assert replacement.id != invitation.id
        assert replacement.token_hash != invitation.token_hash


def test_same_email_can_be_invited_to_different_workspaces(engine: Engine) -> None:
    with Session(engine) as session:
        invitation = seed_invitation(session)
        other_workspace = Workspace(name="Other workspace")
        session.add(other_workspace)
        session.flush()
        other = replacement_for(invitation)
        other.workspace_id = other_workspace.id
        session.add(other)
        session.flush()


def test_token_hash_cannot_be_reused_even_for_revoked_invitation(engine: Engine) -> None:
    with Session(engine) as session:
        invitation = seed_invitation(session)
        invitation.revoked_at = datetime.now(UTC)
        session.flush()
        replacement = replacement_for(invitation)
        replacement.token_hash = invitation.token_hash
        session.add(replacement)
        with pytest.raises(IntegrityError):
            session.flush()


def test_workspace_deletion_cascades_to_invitations(engine: Engine) -> None:
    with Session(engine) as session:
        invitation = seed_invitation(session)
        invitation_id = invitation.id
        workspace = session.get(Workspace, invitation.workspace_id)
        assert workspace is not None
        session.delete(workspace)
        session.flush()
        session.expunge_all()
        assert session.get(WorkspaceInvitation, invitation_id) is None


def test_invitation_migration_preserves_shared_membership_enum() -> None:
    migration = import_module("migrations.versions.c83d21a6f904_create_workspace_invitations")
    upgrade_output, downgrade_output = StringIO(), StringIO()
    for operation, output in (
        (migration.upgrade, upgrade_output),
        (migration.downgrade, downgrade_output),
    ):
        context = MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
        )
        with Operations.context(context):
            operation()
    upgrade_sql = upgrade_output.getvalue()
    downgrade_sql = downgrade_output.getvalue()
    assert "CREATE TABLE workspace_invitations" in upgrade_sql
    assert "role membership_role NOT NULL" in upgrade_sql
    assert "CREATE TYPE" not in upgrade_sql
    assert "WHERE accepted_at IS NULL AND revoked_at IS NULL" in upgrade_sql
    assert "DROP TABLE workspace_invitations" in downgrade_sql
    assert "DROP TYPE" not in downgrade_sql
