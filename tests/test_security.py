from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.dependencies import get_access_token_service
from app.core.security import (
    AccessTokenService,
    InvalidAccessTokenError,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)

# Deliberately public test key. Never use this outside tests.
TEST_SECRET = "campaignhub-test-signing-key-only-" * 2


@pytest.fixture
def token_service() -> AccessTokenService:
    return AccessTokenService(secret_key=SecretStr(TEST_SECRET))


def valid_payload() -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())

    return {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "jti": str(uuid4()),
        "token_type": "access",
        "security_version": 1,
        "iss": "campaignhub",
        "aud": "campaignhub-api",
        "iat": now,
        "nbf": now,
        "exp": now + 900,
    }


def test_password_hash_verifies_only_correct_password() -> None:
    password = "correct horse battery staple"
    stored_hash = hash_password(password)

    assert stored_hash != password
    assert verify_password(password, stored_hash)
    assert not verify_password("incorrect password", stored_hash)


def test_password_hashes_use_independent_salts() -> None:
    password = "correct horse battery staple"

    assert hash_password(password) != hash_password(password)


def test_unknown_password_hash_is_rejected() -> None:
    assert not verify_password("password", "not-a-password-hash")


def test_opaque_tokens_are_distinct_and_hash_consistently() -> None:
    first = generate_opaque_token()
    second = generate_opaque_token()

    assert first != second
    assert hash_token(first) == hash_token(first)
    assert hash_token(first) != hash_token(second)
    assert len(hash_token(first)) == 64
    assert hash_token(first) != first


def test_access_token_round_trip(token_service: AccessTokenService) -> None:
    user_id = uuid4()
    session_id = uuid4()
    token = token_service.create(user_id=user_id, session_id=session_id, security_version=3)
    claims = token_service.decode(token)

    assert claims.sub == user_id
    assert claims.sid == session_id
    assert claims.security_version == 3
    assert claims.token_type == "access"
    assert claims.exp - claims.iat == 900


def test_access_tokens_have_distinct_identifiers(token_service: AccessTokenService) -> None:
    user_id = uuid4()
    first = token_service.create(user_id=user_id, session_id=uuid4(), security_version=1)
    second = token_service.create(user_id=user_id, session_id=uuid4(), security_version=1)

    assert token_service.decode(first).jti != token_service.decode(second).jti


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("iss", "another-issuer"),
        ("aud", "another-audience"),
        ("token_type", "refresh"),
        ("sub", "not-a-uuid"),
        ("sid", "not-a-uuid"),
        ("jti", "not-a-uuid"),
        ("security_version", 0),
        ("security_version", True),
        ("security_version", "1"),
    ],
)
def test_invalid_claims_are_rejected(
    token_service: AccessTokenService,
    field: str,
    value: object,
) -> None:
    payload = valid_payload()
    payload[field] = value
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        token_service.decode(token)


@pytest.mark.parametrize(
    "field",
    ["sub", "sid", "jti", "token_type", "security_version", "iss", "aud", "iat", "nbf", "exp"],
)
def test_missing_claims_are_rejected(token_service: AccessTokenService, field: str) -> None:
    payload = valid_payload()
    del payload[field]
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        token_service.decode(token)


def test_expired_token_is_rejected(token_service: AccessTokenService) -> None:
    payload = valid_payload()
    now = int(datetime.now(UTC).timestamp())
    payload.update(iat=now - 1800, nbf=now - 1800, exp=now - 900)
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        token_service.decode(token)


def test_future_token_is_rejected(token_service: AccessTokenService) -> None:
    payload = valid_payload()
    future = int(datetime.now(UTC).timestamp()) + 600
    payload.update(iat=future, nbf=future, exp=future + 900)
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        token_service.decode(token)


def test_wrong_signing_key_is_rejected(token_service: AccessTokenService) -> None:
    token = jwt.encode(
        valid_payload(),
        "another-test-signing-key-" * 3,
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        token_service.decode(token)


def test_unexpected_algorithm_is_rejected(token_service: AccessTokenService) -> None:
    token = jwt.encode(valid_payload(), TEST_SECRET, algorithm="HS512")

    with pytest.raises(InvalidAccessTokenError):
        token_service.decode(token)


def test_malformed_token_is_rejected(token_service: AccessTokenService) -> None:
    with pytest.raises(InvalidAccessTokenError):
        token_service.decode("not-a-jwt")


def test_short_signing_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        AccessTokenService(secret_key=SecretStr("too-short"))


def test_service_requires_explicit_signing_key() -> None:
    settings = Settings(_env_file=None, jwt_secret_key=None)
    with pytest.raises(ValueError, match="CAMPAIGNHUB_JWT_SECRET_KEY"):
        AccessTokenService.from_settings(settings)


def test_dependency_uses_application_settings() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key=SecretStr(TEST_SECRET),
        jwt_issuer="configured-issuer",
        jwt_audience="configured-audience",
        access_token_lifetime_seconds=120,
    )
    application = FastAPI()
    application.state.settings = settings

    @application.get("/test/token")
    def issue_token(
        service: Annotated[AccessTokenService, Depends(get_access_token_service)],
    ) -> dict[str, str]:
        return {"token": service.create(user_id=uuid4(), session_id=uuid4(), security_version=1)}

    with TestClient(application) as client:
        response = client.get("/test/token")
    assert response.status_code == 200
    claims = AccessTokenService.from_settings(settings).decode(response.json()["token"])
    assert claims.iss == "configured-issuer"
    assert claims.aud == "configured-audience"
    assert claims.exp - claims.iat == 120
    with pytest.raises(InvalidAccessTokenError):
        AccessTokenService(secret_key=SecretStr(TEST_SECRET)).decode(response.json()["token"])
