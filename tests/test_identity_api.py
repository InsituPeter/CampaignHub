from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import Base
from app.core.dependencies import get_session
from app.core.exceptions import RateLimitExceededError
from app.main import create_app
from app.modules.identity.http_dependencies import DeliveryPurpose
from app.modules.identity.service import ActionTokenDelivery

PASSWORD = "a-long-api-test-password"


class CapturingDelivery:
    """Test-only transport: secrets are not printed or included in HTTP responses."""

    def __init__(self) -> None:
        self.messages: list[tuple[DeliveryPurpose, ActionTokenDelivery | None]] = []

    async def enqueue(self, purpose: DeliveryPurpose, delivery: ActionTokenDelivery | None) -> None:
        self.messages.append((purpose, delivery))

    def token(self) -> str:
        prepared = self.messages[-1][1]
        assert prepared is not None
        return prepared.token.get_secret_value()


class TestOnlyLimiter:
    __test__ = False

    def __init__(self) -> None:
        self.deny = False

    async def check(self, request: Request) -> None:
        if self.deny:
            raise RateLimitExceededError()


@dataclass
class API:
    client: AsyncClient
    application: FastAPI
    delivery: CapturingDelivery
    limiter: TestOnlyLimiter


@pytest.fixture
async def api() -> AsyncIterator[API]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    application = create_app(
        Settings(
            _env_file=None,
            environment="test",
            jwt_secret_key=SecretStr("test-only-api-signing-key-123456789012345"),
        )
    )
    application.dependency_overrides[get_session] = test_session
    delivery = CapturingDelivery()
    limiter = TestOnlyLimiter()
    application.state.identity_delivery = delivery
    application.state.identity_rate_limiter = limiter
    try:
        # Lifespan intentionally not run: all DB access uses the isolated fixture.
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            yield API(client, application, delivery, limiter)
    finally:
        await engine.dispose()


async def register_and_verify(api: API) -> None:
    response = await api.client.post(
        "/api/v1/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert response.status_code == 202
    token = api.delivery.token()
    assert token not in response.text
    assert "verification_token" not in response.text
    response = await api.client.post(
        "/api/v1/auth/email-verifications/confirm", json={"token": token}
    )
    assert response.status_code == 200


async def test_complete_identity_http_workflow(api: API) -> None:
    await register_and_verify(api)
    response = await api.client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    login = response.json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    me = await api.client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert set(me.json()) == {"id", "email", "status", "email_verified"}
    response = await api.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert response.status_code == 200
    refreshed = response.json()
    response = await api.client.post(
        "/api/v1/auth/password-resets", json={"email": "owner@example.com"}
    )
    assert response.status_code == 202
    reset = api.delivery.token()
    assert reset not in response.text
    response = await api.client.post(
        "/api/v1/auth/password-resets/confirm",
        json={"token": reset, "new_password": "a-new-api-test-password"},
    )
    assert response.status_code == 200
    assert (await api.client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    response = await api.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refreshed["refresh_token"]}
    )
    assert response.status_code == 401
    response = await api.client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "a-new-api-test-password"},
    )
    assert response.status_code == 200
    login = response.json()
    for _ in range(2):
        response = await api.client.post(
            "/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]}
        )
        assert response.status_code == 200
    response = await api.client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {login['access_token']}"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"


async def test_resend_and_duplicate_registration_use_generic_responses(api: API) -> None:
    body = {"email": "owner@example.com", "password": PASSWORD}
    first = await api.client.post("/api/v1/auth/register", json=body)
    duplicate = await api.client.post("/api/v1/auth/register", json=body)
    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json()
    for path in ("email-verifications", "password-resets"):
        existing = await api.client.post(
            f"/api/v1/auth/{path}", json={"email": "owner@example.com"}
        )
        unknown = await api.client.post(
            f"/api/v1/auth/{path}", json={"email": "unknown@example.com"}
        )
        assert existing.status_code == unknown.status_code == 202
        assert existing.json() == unknown.json()


async def test_logout_all_http_revokes_both_logins(api: API) -> None:
    await register_and_verify(api)
    issued = []
    for _ in range(2):
        response = await api.client.post(
            "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
        )
        issued.append(response.json())
    response = await api.client.post(
        "/api/v1/auth/logout-all", headers={"Authorization": f"Bearer {issued[0]['access_token']}"}
    )
    assert response.status_code == 200
    for login in issued:
        response = await api.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {login['access_token']}"}
        )
        assert response.status_code == 401


async def test_missing_rate_limiter_fails_closed(api: API) -> None:
    del api.application.state.identity_rate_limiter
    response = await api.client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "IDENTITY_RATE_LIMITER_UNAVAILABLE"


async def test_missing_delivery_fails_before_registration(api: API) -> None:
    del api.application.state.identity_delivery
    response = await api.client.post(
        "/api/v1/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "IDENTITY_DELIVERY_UNAVAILABLE"
    api.application.state.identity_delivery = api.delivery
    await register_and_verify(api)  # proves the failed request did not register a user


async def test_limiter_denial_precedes_password_hashing(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_hash(password: str) -> str:
        raise AssertionError("Rate-limited requests must not hash passwords")

    monkeypatch.setattr("app.modules.identity.service.hash_password", forbidden_hash)
    api.limiter.deny = True
    response = await api.client.post(
        "/api/v1/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert response.status_code == 429
    assert api.delivery.messages == []


async def test_validation_does_not_echo_password_and_me_requires_credentials(api: API) -> None:
    response = await api.client.post(
        "/api/v1/auth/register", json={"email": "owner@example.com", "password": "secret123"}
    )
    assert response.status_code == 422
    assert "secret123" not in response.text
    assert (await api.client.get("/api/v1/auth/me")).status_code == 401


async def test_openapi_contains_all_identity_endpoints(api: API) -> None:
    response = await api.client.get("/openapi.json")
    paths = response.json()["paths"]
    for suffix in (
        "register",
        "login",
        "refresh",
        "logout",
        "logout-all",
        "me",
        "email-verifications",
        "email-verifications/confirm",
        "password-resets",
        "password-resets/confirm",
    ):
        assert f"/api/v1/auth/{suffix}" in paths
