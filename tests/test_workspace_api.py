"""HTTP-to-service tests with real JWT authentication and isolated SQLite storage."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.core.dependencies import get_session
from app.core.security import AccessTokenService, hash_token
from app.main import create_app
from app.modules.identity.models import User, UserSession, UserStatus
from app.modules.workspace.model import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Workspace,
    WorkspaceInvitation,
    WorkspaceStatus,
)
from app.modules.workspace.schemas import WorkspaceInvitationCreateRequest
from app.modules.workspace.service import InvitationCreationResult, WorkspaceService


@dataclass
class API:
    client: AsyncClient
    application: FastAPI
    sessions: async_sessionmaker[AsyncSession]
    workspace_id: UUID
    other_workspace_id: UUID
    member_ids: dict[str, UUID]
    user_ids: dict[str, UUID]
    session_ids: dict[str, UUID]
    tokens: dict[str, str]

    def headers(self, who: str = "owner") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    @property
    def path(self) -> str:
        return f"/api/v1/workspaces/{self.workspace_id}"


@pytest.fixture
async def api() -> AsyncIterator[API]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        settings = Settings(
            _env_file=None,
            environment="test",
            jwt_secret_key=SecretStr("workspace-api-tests-only-signing-key-123456"),
        )
        tokens_service = AccessTokenService.from_settings(settings)
        member_ids, user_ids, session_ids, tokens = {}, {}, {}, {}
        now = datetime.now(UTC)
        async with sessions() as session, session.begin():
            # Explicit timestamps keep SQLite storage consistent with bound
            # cursor values (its CURRENT_TIMESTAMP omits fractional seconds).
            workspace = Workspace(name="Main workspace", created_at=now, updated_at=now)
            other_workspace = Workspace(name="Private workspace", created_at=now, updated_at=now)
            session.add_all([workspace, other_workspace])
            await session.flush()
            for who in ("owner", "member", "outsider"):
                email = f"{who}@example.com"
                user = User(
                    email=email,
                    normalized_email=email,
                    password_hash="not-for-responses",
                    status=UserStatus.ACTIVE,
                    email_verified_at=now,
                )
                session.add(user)
                await session.flush()
                login = UserSession(
                    user_id=user.id,
                    security_version=user.security_version,
                    refresh_token_hash=hash_token(f"test-refresh-{who}"),
                    issued_at=now - timedelta(minutes=1),
                    expires_at=now + timedelta(days=1),
                )
                membership = Membership(
                    workspace_id=other_workspace.id if who == "outsider" else workspace.id,
                    user_id=user.id,
                    role=MembershipRole.MARKETER if who == "member" else MembershipRole.OWNER,
                    status=MembershipStatus.ACTIVE,
                    accepted_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add_all([login, membership])
                await session.flush()
                member_ids[who], user_ids[who], session_ids[who] = membership.id, user.id, login.id
                tokens[who] = tokens_service.create(
                    user_id=user.id, session_id=login.id, security_version=user.security_version
                )
            workspace_id, other_workspace_id = workspace.id, other_workspace.id

        async def test_session() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        application = create_app(settings)
        application.dependency_overrides[get_session] = test_session
        # No lifespan, delivery adapter, or production DB connection is used.
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            yield API(
                client,
                application,
                sessions,
                workspace_id,
                other_workspace_id,
                member_ids,
                user_ids,
                session_ids,
                tokens,
            )
    finally:
        await engine.dispose()


OPERATIONS = [
    ("POST", "", {"name": "New workspace"}),
    ("GET", "", None),
    ("GET", "/{workspace}", None),
    ("PATCH", "/{workspace}", {"name": "Changed", "expected_version": 1}),
    ("GET", "/{workspace}/memberships", None),
    ("GET", "/{workspace}/memberships/{member}", None),
    ("PATCH", "/{workspace}/memberships/{member}", {"role": "analyst", "expected_version": 1}),
    ("POST", "/{workspace}/memberships/{member}/suspend", {"expected_version": 1}),
    ("POST", "/{workspace}/memberships/{member}/restore", {"expected_version": 1}),
    ("DELETE", "/{workspace}/memberships/{member}?expected_version=1", None),
    ("POST", "/{workspace}/leave", {"expected_version": 1}),
]


@pytest.mark.parametrize("method,path,body", OPERATIONS)
async def test_all_routes_require_authentication(
    api: API, method: str, path: str, body: dict[str, object] | None
) -> None:
    url = "/api/v1/workspaces" + path.format(
        workspace=api.workspace_id, member=api.member_ids["member"]
    )
    response = await api.client.request(method, url, json=body)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_workspace_create_get_list_update_and_stale_version(api: API) -> None:
    response = await api.client.post(
        "/api/v1/workspaces", headers=api.headers(), json={"name": " New shop ", "slug": "new-shop"}
    )
    assert response.status_code == 201, response.text
    assert isinstance(response.json(), dict)  # Not the accidental one-item tuple/list.
    created = response.json()
    assert created["name"] == "New shop" and created["version"] == 1
    assert response.headers["cache-control"] == "no-store"
    path = f"/api/v1/workspaces/{created['id']}"
    loaded = await api.client.get(path, headers=api.headers())
    assert loaded.status_code == 200 and loaded.json()["id"] == created["id"]
    async with api.sessions() as session:
        owner = await session.scalar(
            select(Membership).where(Membership.workspace_id == UUID(created["id"]))
        )
        assert owner is not None and owner.user_id == api.user_ids["owner"]
        assert owner.role == MembershipRole.OWNER
    listed = await api.client.get(
        "/api/v1/workspaces",
        headers=api.headers(),
        params={"search": "New shop", "role": "owner", "status": "pending"},
    )
    assert [row["id"] for row in listed.json()] == [created["id"]]
    updated = await api.client.patch(
        path, headers=api.headers(), json={"name": "Renamed", "expected_version": 1}
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    stale = await api.client.patch(
        path, headers=api.headers(), json={"name": "Stale", "expected_version": 1}
    )
    assert (
        stale.status_code == 409 and stale.json()["error"]["code"] == "WORKSPACE_VERSION_CONFLICT"
    )


@pytest.mark.parametrize("who", ["owner", "member", "analyst"])
async def test_membership_response_preserves_owner_email_and_hides_it_from_other_roles(
    api: API, who: str
) -> None:
    if who == "analyst":
        async with api.sessions() as session, session.begin():
            row = await session.get(Membership, api.member_ids["member"])
            assert row is not None
            row.role = MembershipRole.ANALYST
    headers = api.headers("owner" if who == "owner" else "member")
    response = await api.client.get(api.path + "/memberships", headers=headers)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 2
    for row in response.json():
        assert ("email" in row) == (who == "owner")
        if who == "owner":
            assert row["email"] in {"owner@example.com", "member@example.com"}
        detail = await api.client.get(api.path + f"/memberships/{row['id']}", headers=headers)
        assert detail.status_code == 200 and detail.json() == row
        assert detail.headers["cache-control"] == "no-store"
    assert "password_hash" not in response.text and "refresh_token" not in response.text


async def test_membership_http_lifecycle_and_immediate_access_changes(api: API) -> None:
    path = api.path + f"/memberships/{api.member_ids['member']}"
    response = await api.client.patch(
        path, headers=api.headers(), json={"role": "analyst", "expected_version": 1}
    )
    assert response.status_code == 200 and response.json()["role"] == "analyst"
    version = response.json()["version"]
    stale = await api.client.patch(
        path, headers=api.headers(), json={"role": "owner", "expected_version": 1}
    )
    assert stale.status_code == 409
    suspended = await api.client.post(
        path + "/suspend", headers=api.headers(), json={"expected_version": version}
    )
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    assert (await api.client.get(api.path, headers=api.headers("member"))).status_code == 404
    restored = await api.client.post(
        path + "/restore",
        headers=api.headers(),
        json={"expected_version": suspended.json()["version"]},
    )
    assert restored.status_code == 200 and restored.json()["role"] == "analyst"
    assert (await api.client.get(api.path, headers=api.headers("member"))).status_code == 200
    removed = await api.client.delete(
        path, headers=api.headers(), params={"expected_version": restored.json()["version"]}
    )
    assert removed.status_code == 200 and removed.json()["removed_at"] is not None
    assert removed.json()["status"] == "removed"
    assert (await api.client.get(api.path, headers=api.headers("member"))).status_code == 404
    forbidden_restore = await api.client.post(
        path + "/restore",
        headers=api.headers(),
        json={"expected_version": removed.json()["version"]},
    )
    assert forbidden_restore.status_code == 409


async def test_member_can_leave_without_removing_owner(api: API) -> None:
    response = await api.client.post(
        api.path + "/leave", headers=api.headers("member"), json={"expected_version": 1}
    )
    assert response.status_code == 200 and response.json()["user_id"] == str(api.user_ids["member"])
    assert response.json()["status"] == "removed"
    assert (await api.client.get(api.path, headers=api.headers())).status_code == 200
    assert (await api.client.get(api.path, headers=api.headers("member"))).status_code == 404


@pytest.mark.parametrize("operation", ["role", "suspend", "remove", "leave"])
async def test_http_last_owner_protection(api: API, operation: str) -> None:
    path = api.path + f"/memberships/{api.member_ids['owner']}"
    if operation == "role":
        response = await api.client.patch(
            path, headers=api.headers(), json={"role": "analyst", "expected_version": 1}
        )
    elif operation == "remove":
        response = await api.client.delete(
            path, headers=api.headers(), params={"expected_version": 1}
        )
    else:
        url = api.path + "/leave" if operation == "leave" else path + "/suspend"
        response = await api.client.post(url, headers=api.headers(), json={"expected_version": 1})
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "WORKSPACE_LAST_OWNER"
    )


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("PATCH", "", {"name": "Unauthorized", "expected_version": 1}),
        ("PATCH", "/memberships/{member}", {"role": "owner", "expected_version": 1}),
        ("POST", "/memberships/{member}/suspend", {"expected_version": 1}),
        ("POST", "/memberships/{member}/restore", {"expected_version": 1}),
        ("DELETE", "/memberships/{member}?expected_version=1", None),
    ],
)
async def test_nonowner_cannot_manage_workspace_or_members(
    api: API, method: str, suffix: str, body: dict[str, object] | None
) -> None:
    response = await api.client.request(
        method,
        api.path + suffix.format(member=api.member_ids["member"]),
        headers=api.headers("member"),
        json=body,
    )
    assert response.status_code == 403


async def test_workspace_and_membership_ids_do_not_grant_access(api: API) -> None:
    for suffix in ("", "/memberships", f"/memberships/{api.member_ids['member']}"):
        response = await api.client.get(api.path + suffix, headers=api.headers("outsider"))
        assert response.status_code == 404
    target = api.path + f"/memberships/{api.member_ids['outsider']}"
    assert (await api.client.get(target, headers=api.headers())).status_code == 404
    response = await api.client.delete(
        target, headers=api.headers(), params={"expected_version": 1}
    )
    assert response.status_code == 404
    response = await api.client.get("/api/v1/workspaces", headers=api.headers())
    assert [row["id"] for row in response.json()] == [str(api.workspace_id)]


@pytest.mark.parametrize(
    "state", ["revoked", "version", "disabled", "unverified", "expired", "tampered"]
)
async def test_existing_bearer_token_does_not_bypass_current_identity(api: API, state: str) -> None:
    headers = api.headers()
    if state == "tampered":
        headers = {"Authorization": "Bearer invalid-token"}
    else:
        async with api.sessions() as session, session.begin():
            user = await session.get(User, api.user_ids["owner"])
            login = await session.get(UserSession, api.session_ids["owner"])
            assert user is not None and login is not None
            if state == "revoked":
                login.revoked_at = datetime.now(UTC)
            elif state == "expired":
                login.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif state == "version":
                user.security_version += 1
            elif state == "unverified":
                user.email_verified_at = None
            else:
                user.status = UserStatus.DISABLED
    assert (await api.client.get(api.path + "/memberships", headers=headers)).status_code == 401


@pytest.mark.parametrize("collection", ["workspaces", "memberships"])
@pytest.mark.parametrize(
    "query,code",
    [
        ({"limit": "0"}, 422),
        ({"limit": "101"}, 422),
        ({"role": "admin"}, 422),
        ({"after_id": "00000000-0000-0000-0000-000000000001"}, 400),
        ({"after_created_at": "2026-10-11T12:00:00Z"}, 400),
        ({"after_id": "bad", "after_created_at": "2026-10-11T12:00:00Z"}, 422),
        (
            {
                "after_id": "00000000-0000-0000-0000-000000000001",
                "after_created_at": "2026-10-11T12:00:00",
            },
            400,
        ),
    ],
)
async def test_collection_query_validation(
    api: API, collection: str, query: dict[str, str], code: int
) -> None:
    path = "/api/v1/workspaces" if collection == "workspaces" else api.path + "/memberships"
    response = await api.client.get(path, headers=api.headers(), params=query)
    assert response.status_code == code, response.text
    assert response.headers["cache-control"] == "no-store"


async def test_membership_cursor_pagination_and_filters(api: API) -> None:
    path = api.path + "/memberships"
    first = await api.client.get(path, headers=api.headers(), params={"limit": 1})
    assert first.status_code == 200 and len(first.json()) == 1
    last = first.json()[0]
    # SQLite drops timezone metadata; PostgreSQL returns aware timestamps.
    cursor_time = datetime.fromisoformat(last["created_at"]).replace(tzinfo=UTC)
    second = await api.client.get(
        path,
        headers=api.headers(),
        params={
            "limit": 1,
            "after_created_at": cursor_time.isoformat(),
            "after_id": last["id"],
        },
    )
    assert second.status_code == 200 and len(second.json()) == 1
    assert second.json()[0]["id"] != last["id"]
    filtered = await api.client.get(
        path, headers=api.headers(), params={"role": "marketer", "status": "active"}
    )
    assert [row["id"] for row in filtered.json()] == [str(api.member_ids["member"])]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"expected_version": 0},
        {"expected_version": True},
        {"expected_version": "1"},
        {"expected_version": 1, "user_id": "injected"},
    ],
)
async def test_mutation_body_validation(api: API, body: dict[str, object]) -> None:
    path = api.path + f"/memberships/{api.member_ids['member']}/suspend"
    response = await api.client.post(path, headers=api.headers(), json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("version", [None, "0", "bad"])
async def test_delete_requires_valid_version_query(api: API, version: str | None) -> None:
    response = await api.client.delete(
        api.path + f"/memberships/{api.member_ids['member']}",
        headers=api.headers(),
        params={} if version is None else {"expected_version": version},
    )
    assert response.status_code == 422


async def test_workspace_cursor_pagination_with_timezone_normalization(api: API) -> None:
    async with api.sessions() as session, session.begin():
        session.add(
            Membership(
                workspace_id=api.other_workspace_id,
                user_id=api.user_ids["owner"],
                role=MembershipRole.OWNER,
                status=MembershipStatus.ACTIVE,
                accepted_at=datetime.now(UTC),
            )
        )
    path = "/api/v1/workspaces"
    first = await api.client.get(path, headers=api.headers(), params={"limit": 1})
    assert first.status_code == 200 and len(first.json()) == 1
    row = first.json()[0]
    cursor_time = datetime.fromisoformat(row["created_at"]).replace(tzinfo=UTC)
    cursor_time = cursor_time.astimezone(timezone(timedelta(hours=1)))
    second = await api.client.get(
        path,
        headers=api.headers(),
        params={"limit": 1, "after_created_at": cursor_time.isoformat(), "after_id": row["id"]},
    )
    assert second.status_code == 200 and len(second.json()) == 1
    assert second.json()[0]["id"] != row["id"]
    row = second.json()[0]
    end = await api.client.get(
        path,
        headers=api.headers(),
        params={
            "after_created_at": datetime.fromisoformat(row["created_at"])
            .replace(tzinfo=UTC)
            .isoformat(),
            "after_id": row["id"],
        },
    )
    assert end.status_code == 200 and end.json() == []


async def test_search_length_and_inactive_directory_access_are_validated(api: API) -> None:
    response = await api.client.get(
        "/api/v1/workspaces", headers=api.headers(), params={"search": "x" * 201}
    )
    assert response.status_code == 422
    response = await api.client.get(
        api.path + "/memberships", headers=api.headers("member"), params={"status": "removed"}
    )
    assert response.status_code == 403


async def test_openapi_registers_all_workspace_operations_and_bearer_auth(api: API) -> None:
    spec = api.application.openapi()
    count = 0
    for path, item in spec["paths"].items():
        if not path.startswith("/api/v1/workspaces"):
            continue
        for method, operation in item.items():
            if method not in {"get", "post", "patch", "delete"}:
                continue
            count += 1
            assert operation["security"] == [{"HTTPBearer": []}]
            assert not {"identity", "service"}.intersection(
                parameter["name"] for parameter in operation.get("parameters", [])
            )
    assert count == 15


async def issue_test_invitation(
    api: API,
    *,
    email: str = "outsider@example.com",
    role: MembershipRole = MembershipRole.MARKETER,
) -> InvitationCreationResult:
    """Test-only issuance through the service, never through disabled HTTP delivery."""
    token_service = AccessTokenService.from_settings(api.application.state.settings)
    actor = token_service.decode(api.tokens["owner"])
    async with api.sessions() as session:
        return await WorkspaceService(session).create_invitation(
            api.workspace_id, WorkspaceInvitationCreateRequest(email=email, role=role), actor=actor
        )


@pytest.mark.parametrize("operation", ["list", "create", "resend", "revoke", "accept"])
async def test_invitation_routes_require_authentication(api: API, operation: str) -> None:
    invitation = await issue_test_invitation(api)
    collection = api.path + "/invitations"
    item = collection + f"/{invitation.invitation.id}"
    if operation == "list":
        response = await api.client.get(collection)
    elif operation == "create":
        response = await api.client.post(
            collection, json={"email": "new@example.com", "role": "analyst"}
        )
    elif operation == "resend":
        response = await api.client.post(item + "/resend")
    elif operation == "revoke":
        response = await api.client.delete(item)
    else:
        response = await api.client.post(
            "/api/v1/invitations/accept", json={"token": invitation.token.get_secret_value()}
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("operation", ["create", "resend"])
@pytest.mark.parametrize("expired", [False, True])
async def test_disabled_delivery_does_not_issue_rotate_or_revoke_tokens(
    api: API,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expired: bool,
) -> None:
    invitation = await issue_test_invitation(api)
    async with api.sessions() as session, session.begin():
        row = await session.get(WorkspaceInvitation, invitation.invitation.id)
        assert row is not None
        if expired:
            row.created_at = datetime.now(UTC) - timedelta(days=3)
            row.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        await session.refresh(row)
        before = (
            row.token_hash,
            row.created_at,
            row.updated_at,
            row.expires_at,
            row.revoked_at,
            row.accepted_at,
            row.accepted_by_user_id,
        )
    create = AsyncMock(side_effect=AssertionError("Disabled HTTP path attempted issuance"))
    resend = AsyncMock(side_effect=AssertionError("Disabled HTTP path attempted rotation"))
    monkeypatch.setattr(WorkspaceService, "create_invitation", create)
    monkeypatch.setattr(WorkspaceService, "resend_invitation", resend)
    # A random app-state adapter cannot accidentally enable an unsafe send path.
    api.application.state.invitation_delivery = object()
    if operation == "create":
        response = await api.client.post(
            api.path + "/invitations",
            headers=api.headers(),
            json={"email": "new@example.com", "role": "analyst"},
        )
    else:
        response = await api.client.post(
            api.path + f"/invitations/{invitation.invitation.id}/resend", headers=api.headers()
        )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "WORKSPACE_INVITATION_DELIVERY_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is False
    assert response.headers["cache-control"] == "no-store"
    assert invitation.token.get_secret_value() not in response.text
    create.assert_not_awaited()
    resend.assert_not_awaited()
    async with api.sessions() as session:
        row = await session.get(WorkspaceInvitation, invitation.invitation.id)
        assert row is not None
        assert (
            row.token_hash,
            row.created_at,
            row.updated_at,
            row.expires_at,
            row.revoked_at,
            row.accepted_at,
            row.accepted_by_user_id,
        ) == before
        assert await session.scalar(select(func.count()).select_from(WorkspaceInvitation)) == 1
        assert await session.scalar(select(func.count()).select_from(Membership)) == 3


@pytest.mark.parametrize("who,expected", [("member", 403), ("outsider", 404)])
@pytest.mark.parametrize("operation", ["list", "create", "resend", "revoke"])
async def test_invitation_management_is_owner_only(
    api: API, who: str, expected: int, operation: str
) -> None:
    invitation = await issue_test_invitation(api)
    collection = api.path + "/invitations"
    item = collection + f"/{invitation.invitation.id}"
    if operation == "list":
        response = await api.client.get(collection, headers=api.headers(who))
    elif operation == "create":
        response = await api.client.post(
            collection,
            headers=api.headers(who),
            json={"email": "new@example.com", "role": "analyst"},
        )
    elif operation == "resend":
        response = await api.client.post(item + "/resend", headers=api.headers(who))
    else:
        response = await api.client.delete(item, headers=api.headers(who))
    assert response.status_code == expected
    assert invitation.token.get_secret_value() not in response.text


@pytest.mark.parametrize("operation", ["resend", "revoke"])
async def test_invitation_id_cannot_cross_workspace_boundary(api: API, operation: str) -> None:
    invitation = await issue_test_invitation(api)
    path = f"/api/v1/workspaces/{api.other_workspace_id}/invitations/{invitation.invitation.id}"
    response = (
        await api.client.post(path + "/resend", headers=api.headers("outsider"))
        if operation == "resend"
        else await api.client.delete(path, headers=api.headers("outsider"))
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", [MembershipRole.MARKETER, MembershipRole.ANALYST])
async def test_matching_verified_recipient_accepts_once_without_token_disclosure(
    api: API,
    role: MembershipRole,
    caplog: pytest.LogCaptureFixture,
) -> None:
    invitation = await issue_test_invitation(api, role=role)
    token = invitation.token.get_secret_value()
    response = await api.client.post(
        "/api/v1/invitations/accept", headers=api.headers("outsider"), json={"token": token}
    )
    assert response.status_code == 200, response.text
    member = response.json()
    assert member["workspace_id"] == str(api.workspace_id)
    assert member["user_id"] == str(api.user_ids["outsider"])
    assert member["status"] == "active" and member["role"] == role.value
    assert token not in response.text and hash_token(token) not in response.text
    assert "token" not in member and "token_hash" not in member
    assert response.headers["cache-control"] == "no-store"
    replay = await api.client.post(
        "/api/v1/invitations/accept", headers=api.headers("outsider"), json={"token": token}
    )
    assert (
        replay.status_code == 400
        and replay.json()["error"]["code"] == "INVALID_WORKSPACE_INVITATION"
    )
    assert token not in caplog.text and hash_token(token) not in caplog.text
    async with api.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.workspace_id == api.workspace_id,
                    Membership.user_id == api.user_ids["outsider"],
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "state,code",
    [
        ("wrong_email", 400),
        ("expired", 400),
        ("revoked", 400),
        ("unknown", 400),
        ("unverified", 401),
        ("suspended_account", 401),
        ("closed_workspace", 400),
    ],
)
async def test_unusable_invitation_or_recipient_cannot_activate_membership(
    api: API, state: str, code: int
) -> None:
    invitation = await issue_test_invitation(api)
    token = invitation.token.get_secret_value()
    who = "member" if state == "wrong_email" else "outsider"
    async with api.sessions() as session, session.begin():
        row = await session.get(WorkspaceInvitation, invitation.invitation.id)
        user = await session.get(User, api.user_ids["outsider"])
        workspace = await session.get(Workspace, api.workspace_id)
        assert row is not None and user is not None and workspace is not None
        now = datetime.now(UTC)
        if state == "expired":
            row.created_at = now - timedelta(days=3)
            row.expires_at = now - timedelta(seconds=1)
        elif state == "revoked":
            row.revoked_at = now
        elif state == "unverified":
            user.email_verified_at = None
        elif state == "suspended_account":
            user.status = UserStatus.SUSPENDED
        elif state == "closed_workspace":
            workspace.status = WorkspaceStatus.CLOSED
        elif state == "unknown":
            token = "unrecognized-invitation-token"
    response = await api.client.post(
        "/api/v1/invitations/accept", headers=api.headers(who), json={"token": token}
    )
    assert response.status_code == code, response.text
    assert token not in response.text
    async with api.sessions() as session:
        row = await session.get(WorkspaceInvitation, invitation.invitation.id)
        assert row is not None and row.accepted_at is None
        assert await session.scalar(select(func.count()).select_from(Membership)) == 3


async def test_invitation_listing_paginates_without_secrets(api: API) -> None:
    first = await issue_test_invitation(api)
    second = await issue_test_invitation(api, email="next@example.com")
    collection = api.path + "/invitations"
    response = await api.client.get(collection, headers=api.headers(), params={"limit": 1})
    assert response.status_code == 200 and len(response.json()) == 1
    last = response.json()[0]
    page = await api.client.get(
        collection,
        headers=api.headers(),
        params={
            "limit": 1,
            "after_created_at": datetime.fromisoformat(last["created_at"])
            .replace(tzinfo=UTC)
            .isoformat(),
            "after_id": last["id"],
        },
    )
    assert page.status_code == 200 and len(page.json()) == 1
    assert {last["id"], page.json()[0]["id"]} == {
        str(first.invitation.id),
        str(second.invitation.id),
    }
    for result in (response, page):
        assert result.headers["cache-control"] == "no-store"
        for invitation in (first, second):
            assert invitation.token.get_secret_value() not in result.text
            assert hash_token(invitation.token.get_secret_value()) not in result.text
        assert "token_hash" not in result.text


async def test_invitation_revocation_is_idempotent_and_blocks_acceptance(api: API) -> None:
    invitation = await issue_test_invitation(api)
    path = api.path + f"/invitations/{invitation.invitation.id}"
    response = await api.client.delete(path, headers=api.headers())
    assert response.status_code == 200 and response.json()["revoked_at"] is not None
    repeated = await api.client.delete(path, headers=api.headers())
    assert repeated.status_code == 200 and repeated.json() == response.json()
    accept = await api.client.post(
        "/api/v1/invitations/accept",
        headers=api.headers("outsider"),
        json={"token": invitation.token.get_secret_value()},
    )
    assert accept.status_code == 400


async def test_accepted_invitation_cannot_be_revoked(api: API) -> None:
    invitation = await issue_test_invitation(api)
    accepted = await api.client.post(
        "/api/v1/invitations/accept",
        headers=api.headers("outsider"),
        json={"token": invitation.token.get_secret_value()},
    )
    assert accepted.status_code == 200
    response = await api.client.delete(
        api.path + f"/invitations/{invitation.invitation.id}", headers=api.headers()
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "WORKSPACE_INVITATION_ACCEPTED"
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"token": ""},
        {"token": "secret" * 100},
        {"token": "valid-looking-token", "user_id": "spoofed"},
    ],
)
async def test_invitation_acceptance_body_is_validated_without_echoing_secrets(
    api: API, body: dict[str, str]
) -> None:
    response = await api.client.post(
        "/api/v1/invitations/accept", headers=api.headers("outsider"), json=body
    )
    assert response.status_code == 422
    if body.get("token"):
        assert body["token"] not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"email": "bad", "role": "analyst"},
        {"email": "new@example.com", "role": "owner"},
        {"email": "new@example.com", "role": "analyst", "invited_by_user_id": "spoofed"},
    ],
)
async def test_disabled_invitation_creation_still_validates_input(
    api: API, body: dict[str, str]
) -> None:
    response = await api.client.post(api.path + "/invitations", headers=api.headers(), json=body)
    assert response.status_code == 422


async def test_invitation_openapi_documents_body_token_and_disabled_issuance(api: API) -> None:
    paths = api.application.openapi()["paths"]
    accept = paths["/api/v1/invitations/accept"]["post"]
    assert "requestBody" in accept and accept["security"] == [{"HTTPBearer": []}]
    assert not any("{token}" in path for path in paths)
    for path in (
        "/api/v1/workspaces/{workspace_id}/invitations",
        "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/resend",
    ):
        assert "503" in paths[path]["post"]["responses"]
