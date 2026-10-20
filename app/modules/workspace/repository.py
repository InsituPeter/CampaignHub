from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User, UserStatus
from app.modules.workspace.model import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Workspace,
    WorkspaceInvitation,
    WorkspaceStatus,
)


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_workspace(
        self,
        *,
        name: str,
        slug: str | None = None,
        timezone: str = "Africa/Lagos",
        default_currency: str = "NGN",
    ) -> Workspace:
        workspace = Workspace(
            name=name, slug=slug, timezone=timezone, default_currency=default_currency
        )
        self.session.add(workspace)
        await self.session.flush()

        return workspace

    async def create_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        role: MembershipRole,
        status: MembershipStatus = MembershipStatus.INVITED,
        invited_by_user_id: UUID | None = None,
        accepted_at: datetime | None = None,
    ) -> Membership:
        membership = Membership(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            status=status,
            invited_by_user_id=invited_by_user_id,
            accepted_at=accepted_at,
        )
        self.session.add(membership)
        await self.session.flush()

        return membership

    async def get_workspace_for_user(
        self, *, workspace_id: UUID, user_id: UUID
    ) -> Workspace | None:
        statement = (
            select(Workspace)
            .join(
                Membership,
                Membership.workspace_id == Workspace.id,
            )
            .where(
                Workspace.id == workspace_id,
                Workspace.archived_at.is_(None),
                Workspace.status != WorkspaceStatus.CLOSED,
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
        return await self.session.scalar(statement)

    async def list_workspaces_for_user(
        self,
        *,
        user_id: UUID,
        status: WorkspaceStatus | None = None,
        role: MembershipRole | None = None,
        search: str | None = None,
        limit: int = 50,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[Workspace]:
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")

        if search is not None:
            search = search.strip()

            if len(search) > 200:
                raise ValueError("Search must not exceed 200 characters")

        # These access restrictions always apply, with or without filters.
        statement = (
            select(Workspace)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.ACTIVE,
                Workspace.archived_at.is_(None),
                Workspace.status != WorkspaceStatus.CLOSED,
            )
        )
        if status is not None:
            statement = statement.where(Workspace.status == status)
        if role is not None:
            statement = statement.where(Membership.role == role)
        if search:
            statement = statement.where(Workspace.name.icontains(search, autoescape=True))
        if after is not None:
            created_at, workspace_id = after
            statement = statement.where(
                tuple_(Workspace.created_at, Workspace.id) > tuple_(created_at, workspace_id)
            )
        statement = statement.order_by(Workspace.created_at.asc(), Workspace.id.asc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_membership(self, *, workspace_id: UUID, user_id: UUID) -> Membership | None:
        statement = select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user_id
        )
        return await self.session.scalar(statement)

    async def get_membership_by_id(
        self, *, workspace_id: UUID, membership_id: UUID
    ) -> Membership | None:
        statement = select(Membership).where(
            Membership.id == membership_id, Membership.workspace_id == workspace_id
        )
        return await self.session.scalar(statement)

    async def list_memberships(
        self,
        *,
        workspace_id: UUID,
        status: MembershipStatus | None = None,
        role: MembershipRole | None = None,
        limit: int = 50,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[tuple[Membership, str]]:
        """Service authorizes visibility; select email, never identity credentials."""
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")
        statement = (
            select(Membership, User.email)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == workspace_id)
        )
        if status is not None:
            statement = statement.where(Membership.status == status)
        if role is not None:
            statement = statement.where(Membership.role == role)
        if after is not None:
            created_at, membership_id = after
            statement = statement.where(
                tuple_(Membership.created_at, Membership.id) > tuple_(created_at, membership_id)
            )
        result = await self.session.execute(
            statement.order_by(Membership.created_at.asc(), Membership.id.asc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        return [(membership, email) for membership, email in result.all()]

    async def get_membership_details(
        self,
        *,
        workspace_id: UUID,
        membership_id: UUID,
    ) -> tuple[Membership, str] | None:
        result = await self.session.execute(
            select(Membership, User.email)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == workspace_id, Membership.id == membership_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return None if row is None else (row[0], row[1])

    async def revoke_invitations_for_email(
        self,
        *,
        workspace_id: UUID,
        normalized_email: str,
        now: datetime,
    ) -> None:
        """Removal requires fresh consent; invalidate outstanding recipient links."""
        await self.session.execute(
            update(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.normalized_email == normalized_email,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=now, updated_at=now)
            .execution_options(synchronize_session="fetch")
        )

    async def lock_workspace(
        self,
        workspace_id: UUID,
    ) -> Workspace | None:
        """Acquire the common workspace lock for membership-changing workflows."""
        statement = (
            select(Workspace)
            .where(Workspace.id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return await self.session.scalar(statement)

    async def lock_membership(
        self, *, workspace_id: UUID, membership_id: UUID
    ) -> Membership | None:
        """Call after locking the workspace, before changing the membership."""
        statement = (
            select(Membership)
            .where(Membership.id == membership_id, Membership.workspace_id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return await self.session.scalar(statement)

    async def count_active_owners(
        self,
        workspace_id: UUID,
    ) -> int:
        """Hold the workspace lock when using this for an owner-change decision."""
        statement = (
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.role == MembershipRole.OWNER,
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
        count = await self.session.scalar(statement)
        return int(count or 0)

    async def count_eligible_owners(self, workspace_id: UUID) -> int:
        """Count active owners with active, verified accounts.

        Hold the workspace lock for membership changes. Account lifecycle
        workflows must also preserve ownership before disabling an owner.
        """
        statement = (
            select(func.count())
            .select_from(Membership)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.role == MembershipRole.OWNER,
                Membership.status == MembershipStatus.ACTIVE,
                User.status == UserStatus.ACTIVE,
                User.email_verified_at.is_not(None),
            )
        )
        return int(await self.session.scalar(statement) or 0)

    async def add_invitation(
        self,
        invitation: WorkspaceInvitation,
    ) -> WorkspaceInvitation:
        """Persist an invitation without committing its transaction."""
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def get_invitation_by_token_hash(
        self,
        *,
        token_hash: str,
    ) -> WorkspaceInvitation | None:
        """Locate an invitation; this does not establish its validity."""
        statement = (
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_open_invitation_for_email(
        self,
        *,
        workspace_id: UUID,
        normalized_email: str,
    ) -> WorkspaceInvitation | None:
        """Include expired invitations that still occupy the unique index."""
        statement = (
            select(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.normalized_email == normalized_email,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def lock_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
    ) -> WorkspaceInvitation | None:
        """Lock and refresh an invitation after acquiring preceding locks."""
        statement = (
            select(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.id == invitation_id,
                WorkspaceInvitation.workspace_id == workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def consume_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        token_hash: str,
        normalized_email: str,
        accepted_by_user_id: UUID,
        now: datetime,
    ) -> bool:
        """Atomically claim an unexpired invitation exactly once.

        The service supplies the authenticated user's ID and normalized
        verified email, never identity values from the request body.
        """
        statement = (
            update(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.id == invitation_id,
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.token_hash == token_hash,
                WorkspaceInvitation.normalized_email == normalized_email,
                WorkspaceInvitation.created_at <= now,
                WorkspaceInvitation.expires_at > now,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
            .values(
                accepted_at=now,
                accepted_by_user_id=accepted_by_user_id,
                updated_at=now,
            )
            .returning(WorkspaceInvitation.id)
            .execution_options(synchronize_session="fetch")
        )
        return await self.session.scalar(statement) is not None

    async def revoke_invitation(
        self,
        *,
        workspace_id: UUID,
        invitation_id: UUID,
        now: datetime,
    ) -> bool:
        """Revoke an open invitation, including one already expired.

        Return False if it is absent, accepted, or already revoked.
        """
        statement = (
            update(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.id == invitation_id,
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=now, updated_at=now)
            .returning(WorkspaceInvitation.id)
            .execution_options(synchronize_session="fetch")
        )
        return await self.session.scalar(statement) is not None

    async def revoke_invitations_by_inviter(
        self,
        *,
        workspace_id: UUID,
        invited_by_user_id: UUID,
        now: datetime,
    ) -> None:
        """Permanently invalidate a sender's open workspace invitations.

        Call in the same transaction that removes their owner access.
        Expired invitations are included so they cannot remain open.
        """
        statement = (
            update(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.invited_by_user_id == invited_by_user_id,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=now, updated_at=now)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.execute(statement)

    async def list_invitations(
        self,
        *,
        workspace_id: UUID,
        limit: int = 50,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[WorkspaceInvitation]:
        """Return invitation history after service-level owner authorization."""
        if not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")

        statement = select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id
        )
        if after is not None:
            created_at, invitation_id = after
            statement = statement.where(
                tuple_(WorkspaceInvitation.created_at, WorkspaceInvitation.id)
                > tuple_(created_at, invitation_id)
            )
        statement = (
            statement.order_by(WorkspaceInvitation.created_at.asc(), WorkspaceInvitation.id.asc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def flush(self) -> None:
        """Persist pending changes without committing the transaction."""
        await self.session.flush()
