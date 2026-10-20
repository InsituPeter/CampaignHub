"""Invitation HTTP endpoints; issuance stays disabled pending durable delivery."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.exceptions import DependencyUnavailableError
from app.core.security import AccessTokenClaims
from app.modules.identity.dependencies import get_current_identity
from app.modules.workspace.http_dependencies import (
    Pagination,
    disable_workspace_cache,
    get_pagination,
    get_workspace_service,
)
from app.modules.workspace.schemas import (
    MembershipResponse,
    WorkspaceInvitationAcceptRequest,
    WorkspaceInvitationCreateRequest,
    WorkspaceInvitationResponse,
)
from app.modules.workspace.service import WorkspaceService

router = APIRouter(tags=["Invitations"], dependencies=[Depends(disable_workspace_cache)])
Identity = Annotated[AccessTokenClaims, Depends(get_current_identity)]
Service = Annotated[WorkspaceService, Depends(get_workspace_service)]
Page = Annotated[Pagination, Depends(get_pagination)]


def delivery_disabled() -> NoReturn:
    # Deliberately not a feature flag or provider stub. Enabling issuance requires
    # transactional, durable delivery integration, not enqueue-after-commit.
    raise DependencyUnavailableError(
        code="WORKSPACE_INVITATION_DELIVERY_UNAVAILABLE",
        message="Invitation email delivery is not configured. No invitation was issued or resent.",
        retryable=False,
    )


@router.get(
    "/workspaces/{workspace_id}/invitations", response_model=list[WorkspaceInvitationResponse]
)
async def list_invitations(
    workspace_id: UUID,
    identity: Identity,
    service: Service,
    page: Page,
) -> list[WorkspaceInvitationResponse]:
    return await service.list_invitations(
        workspace_id, actor=identity, limit=page.limit, after=page.after
    )


@router.post(
    "/workspaces/{workspace_id}/invitations",
    response_model=WorkspaceInvitationResponse,
    responses={503: {"description": "Email delivery is disabled; no invitation is created."}},
)
async def create_invitation(
    workspace_id: UUID,
    command: WorkspaceInvitationCreateRequest,
    identity: Identity,
    service: Service,
) -> NoReturn:
    """Validate input and owner access, but do not issue tokens while disabled."""
    await service.authorize_invitation_management(workspace_id, actor=identity)
    delivery_disabled()


@router.post(
    "/workspaces/{workspace_id}/invitations/{invitation_id}/resend",
    response_model=WorkspaceInvitationResponse,
    responses={
        503: {"description": "Email delivery is disabled; the original invitation is unchanged."}
    },
)
async def resend_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    identity: Identity,
    service: Service,
) -> NoReturn:
    await service.authorize_invitation_management(
        workspace_id, actor=identity, invitation_id=invitation_id
    )
    delivery_disabled()


@router.delete(
    "/workspaces/{workspace_id}/invitations/{invitation_id}",
    response_model=WorkspaceInvitationResponse,
)
async def revoke_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    identity: Identity,
    service: Service,
) -> WorkspaceInvitationResponse:
    return await service.revoke_invitation(workspace_id, invitation_id, actor=identity)


@router.post("/invitations/accept", response_model=MembershipResponse)
async def accept_invitation(
    command: WorkspaceInvitationAcceptRequest,
    identity: Identity,
    service: Service,
) -> MembershipResponse:
    """The signed-in verified user's identity comes from authentication, not JSON."""
    return await service.accept_invitation(command, actor=identity)
