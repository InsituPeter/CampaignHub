"""Shared dependencies for workspace and membership HTTP routes."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.core.exceptions import BadRequestError
from app.modules.workspace.service import WorkspaceService


@dataclass(frozen=True)
class Pagination:
    limit: int
    after: tuple[datetime, UUID] | None


def get_workspace_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceService:
    """Use the request session; the service owns its transactions."""
    return WorkspaceService(session)


def get_pagination(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_created_at: Annotated[
        datetime | None,
        Query(description="Previous page's last created_at, including a timezone offset."),
    ] = None,
    after_id: Annotated[
        UUID | None,
        Query(description="Previous page's last ID; required with after_created_at."),
    ] = None,
) -> Pagination:
    """Validate the service's two-part cursor; it never grants authorization."""
    if (after_created_at is None) != (after_id is None):
        raise BadRequestError(
            code="INVALID_PAGINATION_CURSOR",
            message="Provide after_created_at and after_id together.",
        )

    after = None
    if after_created_at is not None and after_id is not None:
        if after_created_at.utcoffset() is None:
            raise BadRequestError(
                code="INVALID_PAGINATION_CURSOR",
                message="after_created_at must include a timezone offset.",
            )
        after = (after_created_at.astimezone(UTC), after_id)

    return Pagination(limit=limit, after=after)


def disable_workspace_cache(response: Response) -> None:
    """Apply to successful route responses containing private workspace data."""
    response.headers["Cache-Control"] = "no-store"
