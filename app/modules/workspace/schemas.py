from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

from app.modules.workspace.model import (
    MembershipRole,
    MembershipStatus,
    WorkspaceStatus,
)


def normalize_slug(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower()
    return value


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Use a valid IANA timezone, such as Africa/Lagos") from exc

    return value


WorkspaceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
WorkspaceSlug = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
    BeforeValidator(normalize_slug),
]

WorkspaceTimezone = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    AfterValidator(validate_timezone),
]
PilotCurrency = Literal["NGN"]

InvitationRole = Literal[
    MembershipRole.MARKETER,
    MembershipRole.ANALYST,
]

InvitationToken = Annotated[
    SecretStr,
    Field(min_length=1, max_length=512),
]


class WorkspaceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, validate_default=True)


class WorkspaceCreateRequest(WorkspaceCommand):
    name: WorkspaceName
    slug: WorkspaceSlug | None = None
    timezone: WorkspaceTimezone = "Africa/Lagos"
    default_currency: PilotCurrency = "NGN"


class WorkspaceUpdateRequest(WorkspaceCommand):
    expected_version: int = Field(strict=True, ge=1)

    name: WorkspaceName | None = None
    slug: WorkspaceSlug | None = None
    timezone: WorkspaceTimezone | None = None
    default_currency: PilotCurrency | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        changed_fields = self.model_fields_set - {"expected_version"}

        if not changed_fields:
            raise ValueError("Provide at least one workspace field to update")

        for field_name in ("name", "timezone", "default_currency"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str | None
    status: WorkspaceStatus
    timezone: str
    default_currency: PilotCurrency
    version: int

    sending_suspended_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MembershipVersionRequest(WorkspaceCommand):
    """Concurrency precondition for suspend, restore, remove, or leave."""

    expected_version: int = Field(strict=True, ge=1)


class MembershipRoleUpdateRequest(MembershipVersionRequest):
    role: MembershipRole


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: MembershipRole
    status: MembershipStatus
    version: int

    invited_by_user_id: UUID | None
    accepted_at: datetime | None
    removed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OwnerMembershipResponse(MembershipResponse):
    """Owner-only directory representation; never return it to other roles."""

    email: EmailStr = Field(repr=False)


class WorkspaceInvitationCreateRequest(WorkspaceCommand):
    """Owner-submitted invitation details."""

    email: EmailStr = Field(max_length=320, repr=False)
    role: InvitationRole


class WorkspaceInvitationAcceptRequest(WorkspaceCommand):
    """The recipient supplies the token from their invitation."""

    token: InvitationToken = Field(repr=False)


class WorkspaceInvitationResponse(BaseModel):
    """Owner-visible invitation details; never includes credentials."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID

    email: EmailStr = Field(repr=False)
    role: InvitationRole
    invited_by_user_id: UUID

    expires_at: datetime
    accepted_at: datetime | None
    accepted_by_user_id: UUID | None
    revoked_at: datetime | None

    created_at: datetime
    updated_at: datetime
