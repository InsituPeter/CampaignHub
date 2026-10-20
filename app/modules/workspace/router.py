from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.security import AccessTokenClaims
from app.modules.identity.dependencies import get_current_identity
from app.modules.workspace.http_dependencies import (
    Pagination,
    disable_workspace_cache,
    get_pagination,
    get_workspace_service,
)
from app.modules.workspace.model import (
    MembershipRole,
    MembershipStatus,
    WorkspaceStatus,
)
from app.modules.workspace.schemas import (
    MembershipResponse,
    MembershipRoleUpdateRequest,
    MembershipVersionRequest,
    OwnerMembershipResponse,
    WorkspaceCreateRequest,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from app.modules.workspace.service import WorkspaceService

router = APIRouter(
    prefix="/workspaces", tags=["Workspaces"], dependencies=[Depends(disable_workspace_cache)]
)

Service = Annotated[WorkspaceService, Depends(get_workspace_service)]
Identity = Annotated[AccessTokenClaims, Depends(get_current_identity)]
Page = Annotated[Pagination, Depends(get_pagination)]


MembershipView = OwnerMembershipResponse | MembershipResponse


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    command: WorkspaceCreateRequest, identity: Identity, service: Service
) -> WorkspaceResponse:
    return await service.create_workspace(command, actor=identity)


@router.get(
    "",
    response_model=list[WorkspaceResponse],
)
async def list_workspaces(
    identity: Identity,
    service: Service,
    page: Page,
    status: Annotated[WorkspaceStatus | None, Query()] = None,
    role: Annotated[MembershipRole | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> list[WorkspaceResponse]:
    return await service.list_workspace(
        actor=identity, status=status, role=role, search=search, limit=page.limit, after=page.after
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: UUID, identity: Identity, service: Service
) -> WorkspaceResponse:
    return await service.get_workspace(workspace_id, actor=identity)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: UUID, command: WorkspaceUpdateRequest, identity: Identity, service: Service
) -> WorkspaceResponse:
    return await service.update_workspace(workspace_id, command, actor=identity)


@router.get(
    "/{workspace_id}/memberships",
    response_model=list[MembershipView],
    tags=["Memberships"],
)
async def list_memberships(
    workspace_id: UUID,
    identity: Identity,
    service: Service,
    page: Page,
    status: Annotated[MembershipStatus | None, Query()] = None,
    role: Annotated[MembershipRole | None, Query()] = None,
) -> list[MembershipView]:
    return await service.list_memberships(
        workspace_id,
        actor=identity,
        status=status,
        role=role,
        limit=page.limit,
        after=page.after,
    )


@router.get(
    "/{workspace_id}/memberships/{membership_id}",
    response_model=MembershipView,
    tags=["Memberships"],
)
async def get_membership(
    workspace_id: UUID, membership_id: UUID, identity: Identity, service: Service
) -> MembershipView:
    return await service.get_membership(workspace_id, membership_id, actor=identity)


@router.patch(
    "/{workspace_id}/memberships/{membership_id}",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
async def update_membership_role(
    workspace_id: UUID,
    membership_id: UUID,
    command: MembershipRoleUpdateRequest,
    identity: Identity,
    service: Service,
) -> MembershipResponse:
    return await service.update_membership_role(
        workspace_id, membership_id, command, actor=identity
    )


@router.post(
    "/{workspace_id}/memberships/{membership_id}/suspend",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
async def suspend_membership(
    workspace_id: UUID,
    membership_id: UUID,
    command: MembershipVersionRequest,
    identity: Identity,
    service: Service,
) -> MembershipResponse:
    return await service.suspend_membership(workspace_id, membership_id, command, actor=identity)


@router.post(
    "/{workspace_id}/memberships/{membership_id}/restore",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
async def restore_membership(
    workspace_id: UUID,
    membership_id: UUID,
    command: MembershipVersionRequest,
    identity: Identity,
    service: Service,
) -> MembershipResponse:
    return await service.restore_membership(workspace_id, membership_id, command, actor=identity)


@router.delete(
    "/{workspace_id}/memberships/{membership_id}",
    response_model=MembershipResponse,
    tags=["Memberships"],
)
async def remove_membership(
    workspace_id: UUID,
    membership_id: UUID,
    identity: Identity,
    service: Service,
    expected_version: Annotated[int, Query(ge=1)],
) -> MembershipResponse:
    command = MembershipVersionRequest(expected_version=expected_version)

    return await service.remove_membership(workspace_id, membership_id, command, actor=identity)


@router.post("/{workspace_id}/leave", response_model=MembershipResponse, tags=["Memberships"])
async def leave_workspace(
    workspace_id: UUID, command: MembershipVersionRequest, identity: Identity, service: Service
) -> MembershipResponse:
    return await service.leave_workspace(workspace_id, command, actor=identity)
