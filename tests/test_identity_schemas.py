from uuid import uuid4

import pytest
from app.modules.identity.schemas import (
    EmailVerificationConfirmRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from pydantic import ValidationError

from app.modules.identity.models import UserStatus

VALID_PASSWORD = "correct horse battery staple"


def make_user_response() -> UserResponse:
    return UserResponse.model_validate(
        {
            "id": str(uuid4()),
            "email": "owner@example.com",
            "status": "active",
            "email_verified": True,
        }
    )


def test_registration_accepts_valid_input() -> None:
    request = RegisterRequest.model_validate(
        {"email": "owner@example.com", "password": VALID_PASSWORD}
    )
    assert request.email == "owner@example.com"
    assert request.password.get_secret_value() == VALID_PASSWORD


def test_registration_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate({"email": "not-an-email", "password": VALID_PASSWORD})


@pytest.mark.parametrize("password", ["", "short-password", "x" * 129])
def test_registration_enforces_new_password_length(password: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate({"email": "owner@example.com", "password": password})


@pytest.mark.parametrize("length", [15, 128])
def test_registration_accepts_password_length_boundaries(length: int) -> None:
    request = RegisterRequest.model_validate(
        {"email": "owner@example.com", "password": "x" * length}
    )
    assert len(request.password.get_secret_value()) == length


def test_registration_rejects_privilege_injection() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(
            {
                "email": "owner@example.com",
                "password": VALID_PASSWORD,
                "status": "active",
                "security_version": 99,
            }
        )


def test_request_serialization_masks_password() -> None:
    request = RegisterRequest.model_validate(
        {"email": "owner@example.com", "password": VALID_PASSWORD}
    )
    assert VALID_PASSWORD not in repr(request)
    assert VALID_PASSWORD not in request.model_dump_json()


def test_password_whitespace_is_preserved() -> None:
    password = "  correct horse battery staple  "
    request = RegisterRequest.model_validate({"email": "owner@example.com", "password": password})
    assert request.password.get_secret_value() == password


def test_login_accepts_password_below_new_password_minimum() -> None:
    request = LoginRequest.model_validate(
        {"email": "owner@example.com", "password": "older-password"}
    )
    assert request.password.get_secret_value() == "older-password"


def test_login_rejects_empty_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest.model_validate({"email": "owner@example.com", "password": ""})


@pytest.mark.parametrize("token", ["", "x" * 513])
def test_refresh_rejects_invalid_token_length(token: str) -> None:
    with pytest.raises(ValidationError):
        RefreshTokenRequest.model_validate({"refresh_token": token})


def test_refresh_request_masks_token() -> None:
    token = "private-refresh-token"
    request = RefreshTokenRequest.model_validate({"refresh_token": token})
    assert request.refresh_token.get_secret_value() == token
    assert token not in repr(request)
    assert token not in request.model_dump_json()


def test_email_confirmation_requires_token() -> None:
    with pytest.raises(ValidationError):
        EmailVerificationConfirmRequest.model_validate({})


def test_password_reset_applies_new_password_policy() -> None:
    with pytest.raises(ValidationError):
        PasswordResetConfirmRequest.model_validate(
            {"token": "reset-token", "new_password": "short"}
        )


def test_password_reset_accepts_valid_input() -> None:
    request = PasswordResetConfirmRequest.model_validate(
        {"token": "reset-token", "new_password": VALID_PASSWORD}
    )
    assert request.token.get_secret_value() == "reset-token"
    assert request.new_password.get_secret_value() == VALID_PASSWORD


def test_user_response_exposes_only_public_fields() -> None:
    user = make_user_response()
    assert user.status is UserStatus.ACTIVE
    assert set(user.model_dump()) == {"id", "email", "status", "email_verified"}


def test_user_response_rejects_password_hash() -> None:
    payload = make_user_response().model_dump()
    payload["password_hash"] = "private-password-hash"
    with pytest.raises(ValidationError):
        UserResponse.model_validate(payload)


def test_token_response_serializes_tokens_for_client() -> None:
    response = TokenResponse(
        access_token="issued-access-token",
        refresh_token="issued-refresh-token",
        expires_in=900,
        user=make_user_response(),
    )
    payload = response.model_dump(mode="json")
    assert payload["access_token"] == "issued-access-token"
    assert payload["refresh_token"] == "issued-refresh-token"
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 900
    assert "issued-access-token" not in repr(response)
    assert "issued-refresh-token" not in repr(response)


@pytest.mark.parametrize("expires_in", [0, -1])
def test_token_response_requires_positive_lifetime(expires_in: int) -> None:
    with pytest.raises(ValidationError):
        TokenResponse(
            access_token="issued-access-token",
            refresh_token="issued-refresh-token",
            expires_in=expires_in,
            user=make_user_response(),
        )
