from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.modules.workspace.model import MembershipRole, MembershipStatus, WorkspaceStatus
from app.modules.workspace.schemas import (
    MembershipResponse,
    MembershipRoleUpdateRequest,
    MembershipVersionRequest,
    WorkspaceCreateRequest,
    WorkspaceInvitationAcceptRequest,
    WorkspaceInvitationCreateRequest,
    WorkspaceInvitationResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)


@pytest.mark.parametrize("role", list(MembershipRole))
def test_role_update_accepts_explicit_role_and_version(role: MembershipRole) -> None:
    command = MembershipRoleUpdateRequest.model_validate(
        {"role": role.value, "expected_version": 2}
    )
    assert command.role == role and command.expected_version == 2


@pytest.mark.parametrize("version", [0, -1, True, "1", 1.0, None])
def test_role_update_requires_positive_strict_version(version: object) -> None:
    with pytest.raises(ValidationError):
        MembershipRoleUpdateRequest.model_validate({"role": "owner", "expected_version": version})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"role": "owner"},
        {"expected_version": 1},
        {"role": "admin", "expected_version": 1},
        {"role": None, "expected_version": 1},
        {"role": "owner", "expected_version": 1, "status": "active"},
        {"role": "owner", "expected_version": 1, "user_id": "untrusted"},
    ],
)
def test_role_update_rejects_missing_invalid_or_extra_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MembershipRoleUpdateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"expected_version": 0},
        {"expected_version": -1},
        {"expected_version": True},
        {"expected_version": "1"},
        {"expected_version": 1.0},
        {"expected_version": None},
        {"expected_version": 1, "status": "active"},
        {"expected_version": 1, "user_id": "other"},
    ],
)
def test_membership_lifecycle_command_is_strict(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MembershipVersionRequest.model_validate(payload)


def test_membership_lifecycle_command_accepts_version_only() -> None:
    command = MembershipVersionRequest(expected_version=3)
    assert command.model_dump() == {"expected_version": 3}


def test_create_normalizes_name_and_uses_pilot_defaults() -> None:
    command = WorkspaceCreateRequest.model_validate({"name": "  Example Business  "})
    assert command.name == "Example Business"
    assert command.slug is None
    assert command.timezone == "Africa/Lagos"
    assert command.default_currency == "NGN"


def test_create_normalizes_slug_and_timezone() -> None:
    command = WorkspaceCreateRequest.model_validate(
        {"name": "Business", "slug": "  MY-Shop-42  ", "timezone": " Africa/Lagos "}
    )
    assert command.slug == "my-shop-42"
    assert command.timezone == "Africa/Lagos"


@pytest.mark.parametrize("name", ["", "   ", "x" * 201, None])
def test_create_rejects_invalid_names(name: object) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreateRequest.model_validate({"name": name})


@pytest.mark.parametrize(
    "slug", ["", " ", "my shop", "my_shop", "-shop", "shop-", "my--shop", "x" * 101]
)
def test_create_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreateRequest.model_validate({"name": "Business", "slug": slug})


def test_create_accepts_maximum_lengths_and_null_slug() -> None:
    command = WorkspaceCreateRequest.model_validate({"name": "x" * 200, "slug": "s" * 100})
    assert len(command.name) == 200
    assert command.slug is not None and len(command.slug) == 100
    assert WorkspaceCreateRequest.model_validate({"name": "Business", "slug": None}).slug is None


@pytest.mark.parametrize("timezone", ["Africa/Lagos", "UTC", "Europe/London"])
def test_create_accepts_iana_timezones(timezone: str) -> None:
    command = WorkspaceCreateRequest.model_validate({"name": "Business", "timezone": timezone})
    assert command.timezone == timezone


@pytest.mark.parametrize("timezone", ["Afric/Lagos", "Not/AZone", "", "/Africa/Lagos", None])
def test_create_rejects_invalid_timezones(timezone: object) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreateRequest.model_validate({"name": "Business", "timezone": timezone})


@pytest.mark.parametrize("currency", ["USD", "ngn", "", None])
def test_create_rejects_unsupported_currency(currency: object) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreateRequest.model_validate({"name": "Business", "default_currency": currency})


@pytest.mark.parametrize("field", ["status", "role", "owner_id", "business_metadata", "version"])
def test_clients_cannot_set_server_controlled_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceCreateRequest.model_validate({"name": "Business", field: "untrusted"})
    with pytest.raises(ValidationError):
        WorkspaceUpdateRequest.model_validate({"expected_version": 1, field: "untrusted"})


def test_update_only_includes_explicit_changes() -> None:
    command = WorkspaceUpdateRequest.model_validate({"expected_version": 3, "name": " New name "})
    assert command.timezone is None
    assert command.model_dump(exclude_unset=True, exclude={"expected_version"}) == {
        "name": "New name"
    }


def test_update_distinguishes_omitted_slug_from_explicit_null() -> None:
    omitted = WorkspaceUpdateRequest.model_validate({"expected_version": 1, "name": "Business"})
    cleared = WorkspaceUpdateRequest.model_validate({"expected_version": 1, "slug": None})
    assert "slug" not in omitted.model_dump(exclude_unset=True)
    assert cleared.model_dump(exclude_unset=True, exclude={"expected_version"}) == {"slug": None}


@pytest.mark.parametrize("version", [0, -1, True, "1", 1.0, None])
def test_update_requires_strict_positive_integer_version(version: object) -> None:
    with pytest.raises(ValidationError):
        WorkspaceUpdateRequest.model_validate({"expected_version": version, "name": "Business"})


def test_update_requires_version_and_at_least_one_change() -> None:
    with pytest.raises(ValidationError):
        WorkspaceUpdateRequest.model_validate({"name": "Business"})
    with pytest.raises(ValidationError):
        WorkspaceUpdateRequest.model_validate({"expected_version": 1})


@pytest.mark.parametrize("field", ["name", "timezone", "default_currency"])
def test_update_rejects_explicit_null_for_required_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceUpdateRequest.model_validate({"expected_version": 1, field: None})


def test_update_normalizes_and_validates_supplied_values() -> None:
    command = WorkspaceUpdateRequest.model_validate(
        {"expected_version": 1, "slug": " MY-Shop ", "timezone": " UTC ", "default_currency": "NGN"}
    )
    assert command.slug == "my-shop"
    assert command.timezone == "UTC"
    for field, invalid in (
        ("slug", "bad slug"),
        ("timezone", "Not/AZone"),
        ("default_currency", "USD"),
    ):
        with pytest.raises(ValidationError):
            WorkspaceUpdateRequest.model_validate({"expected_version": 1, field: invalid})


def test_workspace_response_reads_attributes_without_leaking_internal_metadata() -> None:
    now = datetime.now(UTC)
    source = SimpleNamespace(
        id=uuid4(),
        name="Business",
        slug=None,
        status=WorkspaceStatus.ACTIVE,
        timezone="Africa/Lagos",
        default_currency="NGN",
        version=2,
        sending_suspended_at=None,
        archived_at=None,
        created_at=now,
        updated_at=now,
        business_metadata={"private": "not-for-this-response"},
    )
    result = WorkspaceResponse.model_validate(source).model_dump(mode="json")
    assert result["id"] == str(source.id)
    assert result["status"] == "active"
    assert result["version"] == 2
    assert "business_metadata" not in result


def test_membership_response_reads_attributes_and_serializes_enums() -> None:
    now = datetime.now(UTC)
    source = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        role=MembershipRole.OWNER,
        status=MembershipStatus.ACTIVE,
        version=1,
        invited_by_user_id=None,
        accepted_at=now,
        removed_at=None,
        created_at=now,
        updated_at=now,
    )
    result = MembershipResponse.model_validate(source).model_dump(mode="json")
    assert result["workspace_id"] == str(source.workspace_id)
    assert result["role"] == "owner"
    assert result["status"] == "active"


@pytest.mark.parametrize("role", ["marketer", "analyst"])
def test_invitation_create_accepts_email_and_allowed_role(role: str) -> None:
    command = WorkspaceInvitationCreateRequest.model_validate(
        {"email": " Teammate@EXAMPLE.COM ", "role": role}
    )
    assert command.email == "Teammate@example.com"
    assert command.role == MembershipRole(role)
    assert command.model_dump(mode="json") == {
        "email": "Teammate@example.com",
        "role": role,
    }
    # Email is available for delivery, but omitted from the command's repr.
    assert str(command.email) not in repr(command)


@pytest.mark.parametrize("role", ["owner", "admin", "MARKETER", "", None])
def test_invitation_create_rejects_disallowed_roles(role: object) -> None:
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationCreateRequest.model_validate(
            {"email": "teammate@example.com", "role": role}
        )
    assert any(error["loc"] == ("role",) for error in caught.value.errors())


@pytest.mark.parametrize("email", ["", " ", "not-an-email", "missing-domain@", "x" * 321, None])
def test_invitation_create_rejects_invalid_email(email: object) -> None:
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationCreateRequest.model_validate({"email": email, "role": "marketer"})
    assert any(error["loc"] == ("email",) for error in caught.value.errors())


@pytest.mark.parametrize("missing_field", ["email", "role"])
def test_invitation_create_requires_email_and_role(missing_field: str) -> None:
    payload = {"email": "teammate@example.com", "role": "analyst"}
    del payload[missing_field]
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationCreateRequest.model_validate(payload)
    assert any(
        error["loc"] == (missing_field,) and error["type"] == "missing"
        for error in caught.value.errors()
    )


@pytest.mark.parametrize(
    "field",
    ["workspace_id", "invited_by_user_id", "expires_at", "token", "token_hash", "normalized_email"],
)
def test_invitation_create_rejects_server_controlled_fields(field: str) -> None:
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationCreateRequest.model_validate(
            {"email": "teammate@example.com", "role": "marketer", field: "untrusted"}
        )
    assert any(
        error["loc"] == (field,) and error["type"] == "extra_forbidden"
        for error in caught.value.errors()
    )


@pytest.mark.parametrize("length", [1, 512])
def test_invitation_accept_allows_token_length_boundaries(length: int) -> None:
    raw_token = "t" * length
    command = WorkspaceInvitationAcceptRequest.model_validate({"token": raw_token})
    assert isinstance(command.token, SecretStr)
    assert command.token.get_secret_value() == raw_token


@pytest.mark.parametrize("token", ["", "t" * 513, None, 123, []])
def test_invitation_accept_rejects_invalid_tokens(token: object) -> None:
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationAcceptRequest.model_validate({"token": token})
    assert any(error["loc"] == ("token",) for error in caught.value.errors())


def test_invitation_accept_requires_token() -> None:
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationAcceptRequest.model_validate({})
    assert any(
        error["loc"] == ("token",) and error["type"] == "missing" for error in caught.value.errors()
    )


@pytest.mark.parametrize("field", ["email", "user_id", "workspace_id", "role", "accepted_at"])
def test_invitation_accept_rejects_client_supplied_identity_and_state(field: str) -> None:
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationAcceptRequest.model_validate(
            {"token": "private-invitation-token", field: "untrusted"}
        )
    assert any(
        error["loc"] == (field,) and error["type"] == "extra_forbidden"
        for error in caught.value.errors()
    )


def test_invitation_token_is_masked_in_representations_and_json() -> None:
    raw_token = "private-invitation-token"
    command = WorkspaceInvitationAcceptRequest.model_validate({"token": raw_token})
    assert raw_token not in repr(command)
    assert raw_token not in str(command)
    assert raw_token not in repr(command.model_dump())
    assert raw_token not in command.model_dump_json()
    assert command.model_dump(mode="json") == {"token": "**********"}
    assert command.token.get_secret_value() == raw_token


def test_invitation_token_is_hidden_in_validation_error_text() -> None:
    raw_token = "sensitive-token-" * 40
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationAcceptRequest.model_validate({"token": raw_token})
    # hide_input_in_errors applies to error text, not errors()'s input values.
    assert raw_token not in str(caught.value)
    assert "input_value=" not in str(caught.value)


@pytest.fixture
def invitation_source() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        email="teammate@example.com",
        normalized_email="teammate@example.com",
        role=MembershipRole.MARKETER,
        invited_by_user_id=uuid4(),
        expires_at=now + timedelta(hours=48),
        accepted_at=None,
        accepted_by_user_id=None,
        revoked_at=None,
        created_at=now,
        updated_at=now,
        token="private-invitation-token",
        token_hash="a" * 64,
    )


@pytest.mark.parametrize("role", [MembershipRole.MARKETER, MembershipRole.ANALYST])
@pytest.mark.parametrize("state", ["pending", "accepted", "revoked"])
def test_invitation_response_serializes_only_public_fields(
    invitation_source: SimpleNamespace, role: MembershipRole, state: str
) -> None:
    invitation_source.role = role
    if state == "accepted":
        invitation_source.accepted_at = invitation_source.created_at + timedelta(minutes=1)
        invitation_source.accepted_by_user_id = uuid4()
    elif state == "revoked":
        invitation_source.revoked_at = invitation_source.created_at + timedelta(minutes=1)

    response = WorkspaceInvitationResponse.model_validate(invitation_source)
    result = response.model_dump(mode="json")
    assert set(result) == {
        "id",
        "workspace_id",
        "email",
        "role",
        "invited_by_user_id",
        "expires_at",
        "accepted_at",
        "accepted_by_user_id",
        "revoked_at",
        "created_at",
        "updated_at",
    }
    assert result["id"] == str(invitation_source.id)
    assert result["workspace_id"] == str(invitation_source.workspace_id)
    assert result["invited_by_user_id"] == str(invitation_source.invited_by_user_id)
    assert result["email"] == invitation_source.email
    assert result["role"] == role.value
    assert response.expires_at == invitation_source.expires_at
    assert response.accepted_at == invitation_source.accepted_at
    assert response.accepted_by_user_id == invitation_source.accepted_by_user_id
    assert response.revoked_at == invitation_source.revoked_at
    assert invitation_source.email not in repr(response)
    assert invitation_source.token not in response.model_dump_json()
    assert invitation_source.token_hash not in response.model_dump_json()


def test_invitation_response_rejects_owner_role(invitation_source: SimpleNamespace) -> None:
    invitation_source.role = MembershipRole.OWNER
    with pytest.raises(ValidationError) as caught:
        WorkspaceInvitationResponse.model_validate(invitation_source)
    assert any(error["loc"] == ("role",) for error in caught.value.errors())
