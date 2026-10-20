"""Explicit deployment dependencies; unavailable protection must fail closed."""

from typing import Annotated, Literal, Protocol, cast

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.core.exceptions import DependencyUnavailableError
from app.modules.identity.service import ActionTokenDelivery, IdentityService

DeliveryPurpose = Literal["email_verification", "password_reset"]


class IdentityDelivery(Protocol):
    async def enqueue(self, purpose: DeliveryPurpose, delivery: ActionTokenDelivery | None) -> None:
        """Durably enqueue delivery; accept None for indistinguishable public no-op requests."""
        ...


class IdentityRateLimiter(Protocol):
    async def check(self, request: Request) -> None:
        """Enforce shared abuse limits before hashing; raise RateLimitExceededError on denial."""
        ...


def get_identity_delivery(request: Request) -> IdentityDelivery:
    delivery = getattr(request.app.state, "identity_delivery", None)
    if delivery is None:
        raise DependencyUnavailableError(code="IDENTITY_DELIVERY_UNAVAILABLE")
    return cast(IdentityDelivery, delivery)


def get_identity_rate_limiter(request: Request) -> IdentityRateLimiter:
    limiter = getattr(request.app.state, "identity_rate_limiter", None)
    if limiter is None:
        raise DependencyUnavailableError(code="IDENTITY_RATE_LIMITER_UNAVAILABLE")
    return cast(IdentityRateLimiter, limiter)


async def protect_identity_route(
    request: Request,
    response: Response,
    limiter: Annotated[IdentityRateLimiter, Depends(get_identity_rate_limiter)],
) -> None:
    response.headers["Cache-Control"] = "no-store"
    await limiter.check(request)


def get_identity_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdentityService:
    return IdentityService(session)
