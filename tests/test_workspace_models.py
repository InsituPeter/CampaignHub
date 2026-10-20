from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.database import Base
from app.modules.identity.models import User
from app.modules.workspace.model import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Workspace,
    WorkspaceStatus,
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def create_membership(session: Session) -> Membership:
    user = User(
        email="owner@example.com", normalized_email="owner@example.com", password_hash="test"
    )
    workspace = Workspace(name="Example business")
    session.add_all([user, workspace])
    session.flush()
    membership = Membership(workspace_id=workspace.id, user_id=user.id, role=MembershipRole.OWNER)
    session.add(membership)
    session.flush()
    return membership


def test_workspace_and_membership_defaults(engine: Engine) -> None:
    with Session(engine) as session:
        membership = create_membership(session)
        workspace = session.get(Workspace, membership.workspace_id)
        assert workspace is not None
        assert workspace.status == WorkspaceStatus.PENDING
        assert workspace.timezone == "Africa/Lagos"
        assert workspace.default_currency == "NGN"
        assert workspace.version == 1
        assert membership.status == MembershipStatus.INVITED
        assert membership.version == 1


@pytest.mark.parametrize(
    "field,value", [("name", " "), ("slug", "UPPER"), ("default_currency", "USD")]
)
def test_workspace_rejects_invalid_fields(engine: Engine, field: str, value: str) -> None:
    with Session(engine) as session:
        workspace = Workspace(name="Business")
        setattr(workspace, field, value)
        session.add(workspace)
        with pytest.raises(IntegrityError):
            session.flush()


def test_workspace_slug_is_unique(engine: Engine) -> None:
    with Session(engine) as session:
        session.add_all(
            [Workspace(name="First", slug="same"), Workspace(name="Second", slug="same")]
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_duplicate_membership_is_rejected(engine: Engine) -> None:
    with Session(engine) as session:
        original = create_membership(session)
        session.add(
            Membership(
                workspace_id=original.workspace_id,
                user_id=original.user_id,
                role=MembershipRole.ANALYST,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("status", [MembershipStatus.ACTIVE, MembershipStatus.REMOVED])
def test_membership_requires_lifecycle_timestamp(engine: Engine, status: MembershipStatus) -> None:
    with Session(engine) as session:
        membership = create_membership(session)
        membership.status = status
        with pytest.raises(IntegrityError):
            session.flush()


def test_membership_can_be_activated_and_removed(engine: Engine) -> None:
    with Session(engine) as session:
        membership = create_membership(session)
        membership.status = MembershipStatus.ACTIVE
        membership.accepted_at = datetime.now(UTC)
        session.flush()
        membership.status = MembershipStatus.REMOVED
        membership.removed_at = datetime.now(UTC)
        session.flush()
        assert membership.version == 3


def test_user_deletion_cannot_silently_remove_memberships(engine: Engine) -> None:
    with Session(engine) as session:
        membership = create_membership(session)
        user = session.get(User, membership.user_id)
        assert user is not None
        session.delete(user)
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize("entity", ["workspace", "membership"])
def test_stale_orm_update_is_rejected(engine: Engine, entity: str) -> None:
    with Session(engine) as setup:
        membership = create_membership(setup)
        workspace_id, membership_id = membership.workspace_id, membership.id
        setup.commit()
    with Session(engine) as first, Session(engine) as second:
        if entity == "workspace":
            first_workspace = first.get(Workspace, workspace_id)
            second_workspace = second.get(Workspace, workspace_id)
            assert first_workspace is not None and second_workspace is not None
            first_workspace.name = "First update"
            first.commit()
            second_workspace.name = "Stale update"
        else:
            first_member = first.get(Membership, membership_id)
            second_member = second.get(Membership, membership_id)
            assert first_member is not None and second_member is not None
            first_member.role = MembershipRole.MARKETER
            first.commit()
            second_member.role = MembershipRole.ANALYST
        with pytest.raises(StaleDataError):
            second.commit()
