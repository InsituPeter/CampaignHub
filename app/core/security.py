import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.core.config import Settings

ALGORITHM = "HS256"

_password_hasher = PasswordHash.recommended()


class InvalidAccessTokenError(Exception):
    """The access token failed authentication checks."""


class AccessTokenClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sub: UUID
    sid: UUID
    jti: UUID
    token_type: Literal["access"]
    security_version: int = Field(strict=True, ge=1)
    iss: str
    aud: str
    iat: int = Field(strict=True)
    nbf: int = Field(strict=True)
    exp: int = Field(strict=True)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password, password_hash)
    except UnknownHashError:
        return False


def generate_opaque_token() -> str:
    """Generate a random refresh, verification, or reset token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a high-entropy opaque token for database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AccessTokenService:
    secret_key: SecretStr
    issuer: str = "campaignhub"
    audience: str = "campaignhub-api"
    lifetime_seconds: int = 900

    @classmethod
    def from_settings(cls, settings: Settings) -> "AccessTokenService":
        if settings.jwt_secret_key is None:
            raise ValueError("Set CAMPAIGNHUB_JWT_SECRET_KEY before using authentication")
        return cls(
            secret_key=settings.jwt_secret_key,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            lifetime_seconds=settings.access_token_lifetime_seconds,
        )

    def __post_init__(self) -> None:
        key = self.secret_key.get_secret_value()

        if len(key.encode("utf-8")) < 32:
            raise ValueError("JWT signing key must contain at least 32 bytes")

        if not self.issuer.strip():
            raise ValueError("JWT issuer cannot be blank")

        if not self.audience.strip():
            raise ValueError("JWT audience cannot be blank")

        if not 1 <= self.lifetime_seconds <= 3600:
            raise ValueError("Access-token lifetime must be 1–3600 seconds")

    def create(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        security_version: int,
    ) -> str:
        now = int(datetime.now(UTC).timestamp())

        claims = AccessTokenClaims(
            sub=user_id,
            sid=session_id,
            jti=uuid4(),
            token_type="access",
            security_version=security_version,
            iss=self.issuer,
            aud=self.audience,
            iat=now,
            nbf=now,
            exp=now + self.lifetime_seconds,
        )

        return jwt.encode(
            claims.model_dump(mode="json"),
            self.secret_key.get_secret_value(),
            algorithm=ALGORITHM,
        )

    def decode(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self.secret_key.get_secret_value(),
                algorithms=[ALGORITHM],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": [
                        "sub",
                        "sid",
                        "jti",
                        "token_type",
                        "security_version",
                        "iss",
                        "aud",
                        "iat",
                        "nbf",
                        "exp",
                    ],
                    "strict_aud": True,
                },
            )

            claims = AccessTokenClaims.model_validate(payload)

            if claims.nbf != claims.iat:
                raise InvalidAccessTokenError("Invalid access token")

            lifetime = claims.exp - claims.iat
            if not 1 <= lifetime <= self.lifetime_seconds:
                raise InvalidAccessTokenError("Invalid access token")

            return claims

        except (InvalidTokenError, ValidationError) as exc:
            raise InvalidAccessTokenError("Invalid access token") from exc
