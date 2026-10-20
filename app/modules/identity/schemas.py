from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.modules.identity.models import UserStatus

NewPassword = Annotated[SecretStr, Field(min_length=15, max_length=128)]

LoginPassword = Annotated[SecretStr, Field(min_length=1, max_length=128)]
OpaqueToken = Annotated[SecretStr, Field(min_length=1, max_length=512)]


class CommandSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RegisterRequest(CommandSchema):
    email: EmailStr
    password: NewPassword


class LoginRequest(CommandSchema):
    email: EmailStr
    password: LoginPassword


class RefreshTokenRequest(CommandSchema):
    refresh_token: OpaqueToken


class LogoutRequest(CommandSchema):
    refresh_token: OpaqueToken


class EmailVerificationRequest(CommandSchema):
    email: EmailStr


class EmailVerificationConfirmRequest(CommandSchema):
    token: OpaqueToken


class PasswordResetRequest(CommandSchema):
    email: EmailStr


class PasswordResetConfirmRequest(CommandSchema):
    token: OpaqueToken
    new_password: NewPassword


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    email: EmailStr
    status: UserStatus
    email_verified: bool


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str = Field(min_length=1, repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(strict=True, gt=0)
    user: UserResponse


class MessageResponse(BaseModel):
    message: str
