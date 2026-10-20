from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.exceptions import (
    AuthenticationRequiredError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    ResourceNotFoundError,
)
from app.core.security import AccessTokenClaims, generate_opaque_token, hash_token
from app.modules.identity.models import User, UserStatus
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.service import normalize_email
from app.modules.workspace.model import (
    Membership,
    MembershipRole,
    MembershipStatus,
    WorkspaceInvitation,
    WorkspaceStatus,
)
from app.modules.workspace.repository import WorkspaceRepository
from app.modules.workspace.schemas import (
    MembershipResponse,
    MembershipRoleUpdateRequest,
    MembershipVersionRequest,
    OwnerMembershipResponse,
    WorkspaceCreateRequest,
    WorkspaceInvitationAcceptRequest,
    WorkspaceInvitationCreateRequest,
    WorkspaceInvitationResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)

INVITATION_LIFETIME = timedelta(hours=48)


class InvalidInvitationError(BadRequestError):
    default_code = "INVALID_WORKSPACE_INVITATION"
    default_message = "The invitation is invalid or no longer usable."


class InvitationAlreadyPendingError(ConflictError):
    default_code = "WORKSPACE_INVITATION_PENDING"
    default_message = (
        "An invitation is already pending for this email. Use the resend action to replace it."
    )


@dataclass(frozen=True)
class InvitationCreationResult:
    """Internal result; never serialize this entire object as an HTTP response."""

    invitation: WorkspaceInvitationResponse
    token: SecretStr = field(repr=False)


def _is_duplicate_invitation_error(error: IntegrityError) -> bool:
    """Recognize only the open-invitation uniqueness violation."""
    current: BaseException | None = error.orig

    while current is not None:
        if (
            getattr(current, "sqlstate", None) == "23505"
            and getattr(current, "constraint_name", None) == "uq_workspace_invitations_open_email"
        ):
            return True
        current = current.__cause__

    return False


class MembershipVersionConflictError(ConflictError):
    default_code = "WORKSPACE_MEMBERSHIP_CHANGED"
    default_message = "The membership changed. Reload and try again."


class LastWorkspaceOwnerError(ConflictError):
    default_code = "WORKSPACE_LAST_OWNER"
    default_message = "The workspace must retain an active, verified owner."


class WorkspaceSlugTakenError(ConflictError):
    default_code = "WORKSPACE_SLUG_TAKEN"
    default_message = "This workspace slug is already in use"


class WorkspaceVersionConflictError(ConflictError):
    default_code = "WORKSPACE_VERSION_CONFLICT"
    default_message = "This workspace has changed. Reload it and retry your update."


def _is_duplicate_slug_error(error: IntegrityError) -> bool:
    """Identify the specific PostgreSQL workspace-slug
    constraint."""
    current: BaseException | None = error.orig

    while current is not None:
        if (
            getattr(current, "sqlstate", None) == "23505"
            and getattr(current, "constraint_name", None) == "uq_workspaces_slug"
        ):
            return True

        current = current.__cause__

    return False


class WorkspaceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WorkspaceRepository(session)
        self.identity_repository = IdentityRepository(session)

    async def create_workspace(
        self, command: WorkspaceCreateRequest, *, actor: AccessTokenClaims
    ) -> WorkspaceResponse:
        try:
            async with self.session.begin():
                user = await self.identity_repository.lock_user(actor.sub)

                if (
                    user is None
                    or user.status != UserStatus.ACTIVE
                    or user.email_verified_at is None
                    or user.security_version != actor.security_version
                ):
                    raise AuthenticationRequiredError()

                now = datetime.now(UTC)
                if actor.exp <= int(now.timestamp()):
                    raise AuthenticationRequiredError()

                session_is_valid = await self.identity_repository.access_session_is_valid(
                    session_id=actor.sid,
                    user_id=user.id,
                    security_version=actor.security_version,
                    now=now,
                )

                if not session_is_valid:
                    raise AuthenticationRequiredError()

                workspace = await self.repository.create_workspace(
                    name=command.name,
                    slug=command.slug,
                    timezone=command.timezone,
                    default_currency=command.default_currency,
                )

                await self.repository.create_membership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role=MembershipRole.OWNER,
                    status=MembershipStatus.ACTIVE,
                    accepted_at=now,
                )
                result = WorkspaceResponse.model_validate(workspace)

                return result

        except IntegrityError as exc:
            if _is_duplicate_slug_error(exc):
                raise WorkspaceSlugTakenError() from None

            raise

    async def _require_active_actor(self, actor: AccessTokenClaims) -> User:
        user = await self.identity_repository.lock_user(actor.sub)

        if (
            user is None
            or user.status != UserStatus.ACTIVE
            or user.email_verified_at is None
            or user.security_version != actor.security_version
        ):
            raise AuthenticationRequiredError()

        now = datetime.now(UTC)

        if actor.exp <= int(now.timestamp()):
            raise AuthenticationRequiredError()

        session_is_valid = await self.identity_repository.access_session_is_valid(
            session_id=actor.sid, user_id=user.id, security_version=actor.security_version, now=now
        )
        if not session_is_valid:
            raise AuthenticationRequiredError()
        return user

    async def update_workspace(
        self,
        workspace_id: UUID,
        command: WorkspaceUpdateRequest,
        *,
        actor: AccessTokenClaims,
    ) -> WorkspaceResponse:
        try:
            async with self.session.begin():
                user = await self._require_active_actor(actor)

                # Consistent lock order: user -> workspace -> membership.
                workspace = await self.repository.lock_workspace(workspace_id)

                if (
                    workspace is None
                    or workspace.archived_at is not None
                    or workspace.status == WorkspaceStatus.CLOSED
                ):
                    raise ResourceNotFoundError(message="Workspace not found.")

                membership = await self.repository.get_membership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                )

                if membership is None:
                    raise ResourceNotFoundError(message="Workspace not found.")

                # Refresh authorization state under a lock, rather than
                # trusting an object already present in the session.
                membership = await self.repository.lock_membership(
                    workspace_id=workspace.id,
                    membership_id=membership.id,
                )

                if membership is None or membership.status != MembershipStatus.ACTIVE:
                    raise ResourceNotFoundError(message="Workspace not found.")

                # The token may have expired while waiting for locks.
                if actor.exp <= int(datetime.now(UTC).timestamp()):
                    raise AuthenticationRequiredError()

                if membership.role != MembershipRole.OWNER:
                    raise ForbiddenError(message="Only workspace owners can update this workspace.")

                if workspace.version != command.expected_version:
                    raise WorkspaceVersionConflictError()

                changes = command.model_dump(
                    exclude_unset=True,
                    exclude={"expected_version"},
                )

                # Status, ownership, and version are not editable here.
                editable_fields = ("name", "slug", "timezone", "default_currency")
                for field_name in editable_fields:
                    if field_name in changes:
                        setattr(workspace, field_name, changes[field_name])

                # The mapper checks and increments version on an UPDATE.
                await self.repository.flush()

                # Load generated values before serialization in async code.
                await self.session.refresh(workspace)
                result = WorkspaceResponse.model_validate(workspace)

            return result

        except StaleDataError:
            raise WorkspaceVersionConflictError() from None

        except IntegrityError as exc:
            if _is_duplicate_slug_error(exc):
                raise WorkspaceSlugTakenError() from None
            raise

    async def update_membership_role(
        self,
        workspace_id: UUID,
        membership_id: UUID,
        command: MembershipRoleUpdateRequest,
        *,
        actor: AccessTokenClaims,
    ) -> MembershipResponse:
        """Change an active member's role while preserving usable ownership.

        Lock affected users in UUID order before the common workspace lock,
        then refresh memberships. Losing ownership permanently revokes invites.
        """
        try:
            async with self.session.begin():
                candidate = await self.repository.get_membership_by_id(
                    workspace_id=workspace_id, membership_id=membership_id
                )
                target_user_id = candidate.user_id if candidate is not None else None
                user_ids = {actor.sub}
                if target_user_id is not None:
                    user_ids.add(target_user_id)
                users = {}
                for user_id in sorted(user_ids):
                    users[user_id] = await self.identity_repository.lock_user(user_id)
                user = await self._require_active_actor(actor)
                workspace = await self.repository.lock_workspace(workspace_id)
                if (
                    workspace is None
                    or workspace.archived_at is not None
                    or workspace.status == WorkspaceStatus.CLOSED
                ):
                    raise ResourceNotFoundError(message="Workspace not found.")
                owner = await self.repository.get_membership(
                    workspace_id=workspace_id, user_id=user.id
                )
                if owner is not None:
                    owner = await self.repository.lock_membership(
                        workspace_id=workspace_id, membership_id=owner.id
                    )
                if owner is None or owner.status != MembershipStatus.ACTIVE:
                    raise ResourceNotFoundError(message="Workspace not found.")
                if owner.role != MembershipRole.OWNER:
                    raise ForbiddenError(message="Only workspace owners can change member roles.")
                membership = await self.repository.lock_membership(
                    workspace_id=workspace_id, membership_id=membership_id
                )
                if membership is None:
                    raise ResourceNotFoundError(message="Membership not found.")
                if membership.user_id != target_user_id:
                    raise MembershipVersionConflictError()

                now = datetime.now(UTC)
                if actor.exp <= int(now.timestamp()) or not (
                    await self.identity_repository.access_session_is_valid(
                        session_id=actor.sid,
                        user_id=user.id,
                        security_version=actor.security_version,
                        now=now,
                    )
                ):
                    raise AuthenticationRequiredError()
                if membership.version != command.expected_version:
                    raise MembershipVersionConflictError()
                if membership.status != MembershipStatus.ACTIVE:
                    raise ConflictError(
                        code="WORKSPACE_MEMBERSHIP_INACTIVE",
                        message="Only active memberships can have their role changed.",
                    )

                if membership.role != command.role:
                    target_user = users.get(membership.user_id)
                    if command.role == MembershipRole.OWNER and (
                        target_user is None
                        or target_user.status != UserStatus.ACTIVE
                        or target_user.email_verified_at is None
                    ):
                        raise ConflictError(
                            code="WORKSPACE_OWNER_INELIGIBLE",
                            message="An owner must have an active, verified account.",
                        )
                    if membership.role == MembershipRole.OWNER:
                        # A disabled/unverified target does not count as a usable owner.
                        target_is_eligible = (
                            target_user is not None
                            and target_user.status == UserStatus.ACTIVE
                            and target_user.email_verified_at is not None
                        )
                        eligible_owners = await self.repository.count_eligible_owners(workspace_id)
                        if eligible_owners - int(target_is_eligible) < 1:
                            raise LastWorkspaceOwnerError()
                        await self.repository.revoke_invitations_by_inviter(
                            workspace_id=workspace_id,
                            invited_by_user_id=membership.user_id,
                            now=now,
                        )
                    membership.role = command.role
                    await self.repository.flush()
                    await self.session.refresh(membership)
                result = MembershipResponse.model_validate(membership)
            return result
        except StaleDataError:
            raise MembershipVersionConflictError() from None

    async def _require_current_session(self, actor: AccessTokenClaims, now: datetime) -> None:
        if actor.exp <= int(now.timestamp()) or not (
            await self.identity_repository.access_session_is_valid(
                session_id=actor.sid,
                user_id=actor.sub,
                security_version=actor.security_version,
                now=now,
            )
        ):
            raise AuthenticationRequiredError()

    async def _lock_member_actor(
        self,
        workspace_id: UUID,
        actor: AccessTokenClaims,
        *,
        allow_suspended: bool = False,
    ) -> Membership:
        """Caller already holds the actor's user lock and owns the transaction."""
        workspace = await self.repository.lock_workspace(workspace_id)
        if (
            workspace is None
            or workspace.archived_at is not None
            or workspace.status == WorkspaceStatus.CLOSED
        ):
            raise ResourceNotFoundError(message="Workspace not found.")
        member = await self.repository.get_membership(workspace_id=workspace_id, user_id=actor.sub)
        if member is not None:
            member = await self.repository.lock_membership(
                workspace_id=workspace_id, membership_id=member.id
            )
        allowed = {MembershipStatus.ACTIVE}
        if allow_suspended:
            allowed.add(MembershipStatus.SUSPENDED)
        if member is None or member.status not in allowed:
            raise ResourceNotFoundError(message="Workspace not found.")
        return member

    async def _change_membership_status(
        self,
        workspace_id: UUID,
        membership_id: UUID | None,
        command: MembershipVersionRequest,
        *,
        actor: AccessTokenClaims,
        status: MembershipStatus,
        leaving: bool = False,
    ) -> MembershipResponse:
        try:
            async with self.session.begin():
                candidate = None
                if leaving:
                    candidate = await self.repository.get_membership(
                        workspace_id=workspace_id, user_id=actor.sub
                    )
                elif membership_id is not None:
                    candidate = await self.repository.get_membership_by_id(
                        workspace_id=workspace_id, membership_id=membership_id
                    )
                target_user_id = candidate.user_id if candidate is not None else None
                target_id = candidate.id if candidate is not None else membership_id
                user_ids = {actor.sub}
                if target_user_id is not None:
                    user_ids.add(target_user_id)
                users: dict[UUID, User | None] = {}
                for user_id in sorted(user_ids):
                    users[user_id] = await self.identity_repository.lock_user(user_id)
                await self._require_active_actor(actor)
                actor_member = await self._lock_member_actor(
                    workspace_id, actor, allow_suspended=leaving
                )
                if not leaving and actor_member.role != MembershipRole.OWNER:
                    raise ForbiddenError(message="Only workspace owners can manage memberships.")
                membership = (
                    None
                    if target_id is None
                    else await self.repository.lock_membership(
                        workspace_id=workspace_id, membership_id=target_id
                    )
                )
                if membership is None:
                    raise ResourceNotFoundError(message="Membership not found.")
                if membership.user_id != target_user_id or (
                    leaving and membership.user_id != actor.sub
                ):
                    raise MembershipVersionConflictError()
                now = datetime.now(UTC)
                await self._require_current_session(actor, now)
                if membership.version != command.expected_version:
                    raise MembershipVersionConflictError()
                if membership.status == status:
                    return MembershipResponse.model_validate(membership)

                allowed_sources = {
                    MembershipStatus.SUSPENDED: {MembershipStatus.ACTIVE},
                    MembershipStatus.ACTIVE: {MembershipStatus.SUSPENDED},
                    MembershipStatus.REMOVED: {
                        MembershipStatus.ACTIVE,
                        MembershipStatus.SUSPENDED,
                        MembershipStatus.INVITED,
                    },
                }
                if membership.status not in allowed_sources[status]:
                    raise ConflictError(
                        code="WORKSPACE_MEMBERSHIP_INVALID_TRANSITION",
                        message=(
                            "This membership cannot make that transition. "
                            "Removed members need a new invitation."
                        ),
                    )
                target_user = users.get(membership.user_id)
                eligible = (
                    target_user is not None
                    and target_user.status == UserStatus.ACTIVE
                    and target_user.email_verified_at is not None
                )
                if status == MembershipStatus.ACTIVE and not eligible:
                    raise ConflictError(
                        code="WORKSPACE_MEMBER_INELIGIBLE",
                        message="Restoration requires an active, verified account.",
                    )
                if (
                    membership.status == MembershipStatus.ACTIVE
                    and membership.role == MembershipRole.OWNER
                ):
                    owners = await self.repository.count_eligible_owners(workspace_id)
                    if owners - int(eligible) < 1:
                        raise LastWorkspaceOwnerError()
                if status in {MembershipStatus.SUSPENDED, MembershipStatus.REMOVED}:
                    await self.repository.revoke_invitations_by_inviter(
                        workspace_id=workspace_id, invited_by_user_id=membership.user_id, now=now
                    )
                if status == MembershipStatus.REMOVED and target_user is not None:
                    await self.repository.revoke_invitations_for_email(
                        workspace_id=workspace_id,
                        normalized_email=target_user.normalized_email,
                        now=now,
                    )
                membership.status = status
                membership.removed_at = now if status == MembershipStatus.REMOVED else None
                if status == MembershipStatus.ACTIVE and membership.accepted_at is None:
                    # Do not manufacture acceptance for inconsistent legacy rows.
                    raise ConflictError(
                        code="WORKSPACE_MEMBERSHIP_INVALID_TRANSITION",
                        message=(
                            "This membership has no recorded acceptance. Send a new invitation."
                        ),
                    )
                await self.repository.flush()
                await self.session.refresh(membership)
                result = MembershipResponse.model_validate(membership)
            return result
        except StaleDataError:
            raise MembershipVersionConflictError() from None

    async def suspend_membership(
        self,
        workspace_id: UUID,
        membership_id: UUID,
        command: MembershipVersionRequest,
        *,
        actor: AccessTokenClaims,
    ) -> MembershipResponse:
        return await self._change_membership_status(
            workspace_id, membership_id, command, actor=actor, status=MembershipStatus.SUSPENDED
        )

    async def restore_membership(
        self,
        workspace_id: UUID,
        membership_id: UUID,
        command: MembershipVersionRequest,
        *,
        actor: AccessTokenClaims,
    ) -> MembershipResponse:
        return await self._change_membership_status(
            workspace_id, membership_id, command, actor=actor, status=MembershipStatus.ACTIVE
        )

    async def remove_membership(
        self,
        workspace_id: UUID,
        membership_id: UUID,
        command: MembershipVersionRequest,
        *,
        actor: AccessTokenClaims,
    ) -> MembershipResponse:
        return await self._change_membership_status(
            workspace_id, membership_id, command, actor=actor, status=MembershipStatus.REMOVED
        )

    async def leave_workspace(
        self,
        workspace_id: UUID,
        command: MembershipVersionRequest,
        *,
        actor: AccessTokenClaims,
    ) -> MembershipResponse:
        """Leave one's own active/suspended membership, never another user's."""
        return await self._change_membership_status(
            workspace_id, None, command, actor=actor, status=MembershipStatus.REMOVED, leaving=True
        )

    @staticmethod
    def _membership_view(
        membership: Membership,
        email: str,
        *,
        owner: bool,
    ) -> MembershipResponse | OwnerMembershipResponse:
        public = MembershipResponse.model_validate(membership)
        if owner:
            return OwnerMembershipResponse(**public.model_dump(), email=email)
        return public

    async def list_memberships(
        self,
        workspace_id: UUID,
        *,
        actor: AccessTokenClaims,
        status: MembershipStatus | None = None,
        role: MembershipRole | None = None,
        limit: int = 50,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[MembershipResponse | OwnerMembershipResponse]:
        """Owners see all states/emails; other active members see the active roster."""
        async with self.session.begin():
            await self._require_active_actor(actor)
            member = await self._lock_member_actor(workspace_id, actor)
            await self._require_current_session(actor, datetime.now(UTC))
            owner = member.role == MembershipRole.OWNER
            if not owner:
                if status not in {None, MembershipStatus.ACTIVE}:
                    raise ForbiddenError(
                        message="Only workspace owners can view inactive memberships."
                    )
                status = MembershipStatus.ACTIVE
            rows = await self.repository.list_memberships(
                workspace_id=workspace_id, status=status, role=role, limit=limit, after=after
            )
            result = [self._membership_view(row, email, owner=owner) for row, email in rows]
        return result

    async def get_membership(
        self,
        workspace_id: UUID,
        membership_id: UUID,
        *,
        actor: AccessTokenClaims,
    ) -> MembershipResponse | OwnerMembershipResponse:
        async with self.session.begin():
            await self._require_active_actor(actor)
            member = await self._lock_member_actor(workspace_id, actor)
            await self._require_current_session(actor, datetime.now(UTC))
            owner = member.role == MembershipRole.OWNER
            details = await self.repository.get_membership_details(
                workspace_id=workspace_id, membership_id=membership_id
            )
            if details is None or (not owner and details[0].status != MembershipStatus.ACTIVE):
                raise ResourceNotFoundError(message="Membership not found.")
            result = self._membership_view(*details, owner=owner)
        return result

    async def authorize_invitation_management(
        self,
        workspace_id: UUID,
        *,
        actor: AccessTokenClaims,
        invitation_id: UUID | None = None,
    ) -> None:
        """Read-only preflight for currently disabled HTTP delivery operations.

        This does not grant future write authority; issuing services recheck
        authorization in their own transaction when delivery is implemented.
        """
        async with self.session.begin():
            await self._require_active_actor(actor)
            member = await self._lock_member_actor(workspace_id, actor)
            if member.role != MembershipRole.OWNER:
                raise ForbiddenError(message="Only workspace owners can manage invitations.")
            if invitation_id is not None:
                invitation = await self.repository.lock_invitation(
                    workspace_id=workspace_id, invitation_id=invitation_id
                )
                if invitation is None:
                    raise ResourceNotFoundError(message="Invitation not found.")
            await self._require_current_session(actor, datetime.now(UTC))

    async def create_invitation(
        self,
        workspace_id: UUID,
        command: WorkspaceInvitationCreateRequest,
        *,
        actor: AccessTokenClaims,
    ) -> InvitationCreationResult:
        email = str(command.email)
        normalized_email = normalize_email(email)

        try:
            async with self.session.begin():
                user = await self._require_active_actor(actor)

                # All membership/invitation mutations must acquire the
                # workspace lock before changing workspace access.
                workspace = await self.repository.lock_workspace(workspace_id)
                if (
                    workspace is None
                    or workspace.archived_at is not None
                    or workspace.status == WorkspaceStatus.CLOSED
                ):
                    raise ResourceNotFoundError(message="Workspace not found.")

                owner_membership = await self.repository.get_membership(
                    workspace_id=workspace.id, user_id=user.id
                )
                if owner_membership is None:
                    raise ResourceNotFoundError(message="Workspace not found.")

                owner_membership = await self.repository.lock_membership(
                    workspace_id=workspace.id, membership_id=owner_membership.id
                )
                if owner_membership is None or owner_membership.status != MembershipStatus.ACTIVE:
                    raise ResourceNotFoundError(message="Workspace not found.")
                if owner_membership.role != MembershipRole.OWNER:
                    raise ForbiddenError(message="Only workspace owners can send invitations.")

                # A recipient may not have registered yet.
                recipient = await self.identity_repository.get_user_by_email(normalized_email)
                recipient_membership = None
                if recipient is not None:
                    candidate = await self.repository.get_membership(
                        workspace_id=workspace.id, user_id=recipient.id
                    )
                    if candidate is not None:
                        recipient_membership = await self.repository.lock_membership(
                            workspace_id=workspace.id, membership_id=candidate.id
                        )

                existing = await self.repository.get_open_invitation_for_email(
                    workspace_id=workspace.id, normalized_email=normalized_email
                )
                if existing is not None:
                    existing = await self.repository.lock_invitation(
                        workspace_id=workspace.id, invitation_id=existing.id
                    )

                # Recheck time-dependent authentication after lock waits.
                now = datetime.now(UTC)
                if actor.exp <= int(now.timestamp()):
                    raise AuthenticationRequiredError()
                session_is_valid = await self.identity_repository.access_session_is_valid(
                    session_id=actor.sid,
                    user_id=user.id,
                    security_version=actor.security_version,
                    now=now,
                )
                if not session_is_valid:
                    raise AuthenticationRequiredError()

                if recipient_membership is not None:
                    if recipient_membership.status == MembershipStatus.ACTIVE:
                        raise ConflictError(
                            code="WORKSPACE_MEMBER_ALREADY_ACTIVE",
                            message=(
                                "This user is already an active workspace member. "
                                "Use the role-management action instead."
                            ),
                        )
                    if recipient_membership.status == MembershipStatus.SUSPENDED:
                        raise ConflictError(
                            code="WORKSPACE_MEMBER_SUSPENDED",
                            message=("Restore this member's access before issuing an invitation."),
                        )

                if (
                    existing is not None
                    and existing.accepted_at is None
                    and existing.revoked_at is None
                ):
                    expires_at = existing.expires_at
                    # PostgreSQL returns aware timestamps; SQLite tests
                    # return naive timestamps for values stored in UTC.
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=UTC)
                    if expires_at > now:
                        raise InvitationAlreadyPendingError()

                    # Expired open rows still occupy the unique index.
                    revoked = await self.repository.revoke_invitation(
                        workspace_id=workspace.id, invitation_id=existing.id, now=now
                    )
                    if not revoked:
                        raise ConflictError(
                            code="WORKSPACE_INVITATION_CHANGED",
                            message="The invitation changed. Reload and try again.",
                        )

                raw_token = generate_opaque_token()
                invitation = await self.repository.add_invitation(
                    WorkspaceInvitation(
                        workspace_id=workspace.id,
                        email=email,
                        normalized_email=normalized_email,
                        role=command.role,
                        invited_by_user_id=user.id,
                        token_hash=hash_token(raw_token),
                        created_at=now,
                        updated_at=now,
                        expires_at=now + INVITATION_LIFETIME,
                    )
                )
                result = InvitationCreationResult(
                    invitation=WorkspaceInvitationResponse.model_validate(invitation),
                    token=SecretStr(raw_token),
                )

            return result

        except IntegrityError as exc:
            if _is_duplicate_invitation_error(exc):
                raise InvitationAlreadyPendingError() from None
            raise

    async def resend_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        actor: AccessTokenClaims,
    ) -> InvitationCreationResult:
        """Replace an open invitation atomically; delivery is the caller's concern.

        The replacement has a new ID and token, preserves email and role, and
        records the current owner as sender. Never revive a revoked invitation.
        """
        try:
            async with self.session.begin():
                user = await self._require_active_actor(actor)
                workspace = await self.repository.lock_workspace(workspace_id)
                if (
                    workspace is None
                    or workspace.archived_at is not None
                    or workspace.status == WorkspaceStatus.CLOSED
                ):
                    raise ResourceNotFoundError(message="Workspace not found.")

                owner = await self.repository.get_membership(
                    workspace_id=workspace_id, user_id=user.id
                )
                if owner is not None:
                    owner = await self.repository.lock_membership(
                        workspace_id=workspace_id, membership_id=owner.id
                    )
                if owner is None or owner.status != MembershipStatus.ACTIVE:
                    raise ResourceNotFoundError(message="Workspace not found.")
                if owner.role != MembershipRole.OWNER:
                    raise ForbiddenError(message="Only workspace owners can resend invitations.")

                invitation = await self.repository.lock_invitation(
                    workspace_id=workspace_id, invitation_id=invitation_id
                )
                if invitation is None:
                    raise ResourceNotFoundError(message="Invitation not found.")
                if invitation.accepted_at is not None or invitation.revoked_at is not None:
                    raise ConflictError(
                        code="WORKSPACE_INVITATION_CHANGED",
                        message="Only open invitations can be resent. Reload and try again.",
                    )

                # The workspace lock serializes all invitation/membership writes.
                # The recipient need not have registered yet.
                recipient = await self.identity_repository.get_user_by_email(
                    invitation.normalized_email
                )
                membership = None
                if recipient is not None:
                    membership = await self.repository.get_membership(
                        workspace_id=workspace_id, user_id=recipient.id
                    )
                    if membership is not None:
                        membership = await self.repository.lock_membership(
                            workspace_id=workspace_id, membership_id=membership.id
                        )

                now = datetime.now(UTC)
                if actor.exp <= int(now.timestamp()) or not (
                    await self.identity_repository.access_session_is_valid(
                        session_id=actor.sid,
                        user_id=user.id,
                        security_version=actor.security_version,
                        now=now,
                    )
                ):
                    raise AuthenticationRequiredError()
                if membership is not None:
                    if membership.status == MembershipStatus.ACTIVE:
                        raise ConflictError(
                            code="WORKSPACE_MEMBER_ALREADY_ACTIVE",
                            message="This user is already an active workspace member.",
                        )
                    if membership.status == MembershipStatus.SUSPENDED:
                        raise ConflictError(
                            code="WORKSPACE_MEMBER_SUSPENDED",
                            message="Restore this member's access before issuing an invitation.",
                        )

                created_at = invitation.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                if created_at > now:
                    raise InvalidInvitationError()
                revoked = await self.repository.revoke_invitation(
                    workspace_id=workspace_id, invitation_id=invitation_id, now=now
                )
                if not revoked:
                    raise ConflictError(
                        code="WORKSPACE_INVITATION_CHANGED",
                        message="The invitation changed. Reload and try again.",
                    )

                raw_token = generate_opaque_token()
                replacement = await self.repository.add_invitation(
                    WorkspaceInvitation(
                        workspace_id=workspace_id,
                        email=invitation.email,
                        normalized_email=invitation.normalized_email,
                        role=invitation.role,
                        invited_by_user_id=user.id,
                        token_hash=hash_token(raw_token),
                        created_at=now,
                        updated_at=now,
                        expires_at=now + INVITATION_LIFETIME,
                    )
                )
                result = InvitationCreationResult(
                    invitation=WorkspaceInvitationResponse.model_validate(replacement),
                    token=SecretStr(raw_token),
                )
            return result
        except IntegrityError as exc:
            if _is_duplicate_invitation_error(exc):
                raise InvitationAlreadyPendingError() from None
            raise

    async def revoke_invitation(
        self,
        workspace_id: UUID,
        invitation_id: UUID,
        *,
        actor: AccessTokenClaims,
    ) -> WorkspaceInvitationResponse:
        """Revoke an open invitation, or return its existing revoked state.

        Accepted invitations require a separate membership-management action.
        Authorization is checked even when repeating an earlier revocation.
        """
        async with self.session.begin():
            user = await self._require_active_actor(actor)
            workspace = await self.repository.lock_workspace(workspace_id)
            if (
                workspace is None
                or workspace.archived_at is not None
                or workspace.status == WorkspaceStatus.CLOSED
            ):
                raise ResourceNotFoundError(message="Workspace not found.")

            owner = await self.repository.get_membership(workspace_id=workspace_id, user_id=user.id)
            if owner is not None:
                owner = await self.repository.lock_membership(
                    workspace_id=workspace_id, membership_id=owner.id
                )
            if owner is None or owner.status != MembershipStatus.ACTIVE:
                raise ResourceNotFoundError(message="Workspace not found.")
            if owner.role != MembershipRole.OWNER:
                raise ForbiddenError(message="Only workspace owners can revoke invitations.")

            invitation = await self.repository.lock_invitation(
                workspace_id=workspace_id, invitation_id=invitation_id
            )
            if invitation is None:
                raise ResourceNotFoundError(message="Invitation not found.")

            # Locks may have taken long enough for authentication to expire.
            now = datetime.now(UTC)
            if actor.exp <= int(now.timestamp()) or not (
                await self.identity_repository.access_session_is_valid(
                    session_id=actor.sid,
                    user_id=user.id,
                    security_version=actor.security_version,
                    now=now,
                )
            ):
                raise AuthenticationRequiredError()
            if invitation.accepted_at is not None:
                raise ConflictError(
                    code="WORKSPACE_INVITATION_ACCEPTED",
                    message="An accepted invitation cannot be revoked. Manage membership instead.",
                )

            if invitation.revoked_at is None:
                created_at = invitation.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                if created_at > now:
                    raise InvalidInvitationError()
                revoked = await self.repository.revoke_invitation(
                    workspace_id=workspace_id, invitation_id=invitation_id, now=now
                )
                if not revoked:
                    raise ConflictError(
                        code="WORKSPACE_INVITATION_CHANGED",
                        message="The invitation changed. Reload and try again.",
                    )
                await self.session.refresh(invitation)
            result = WorkspaceInvitationResponse.model_validate(invitation)
        return result

    async def list_invitations(
        self,
        workspace_id: UUID,
        *,
        actor: AccessTokenClaims,
        limit: int = 50,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[WorkspaceInvitationResponse]:
        """Return owner-visible history ordered by (created_at, id).

        Pass the last item's (created_at, id) as the next page's cursor.
        Cursors are ordering boundaries, not authorization credentials or
        snapshots across requests. All invitation lifecycle states are included.
        """
        async with self.session.begin():
            user = await self._require_active_actor(actor)
            # Keep owner authorization stable while reading private email details.
            workspace = await self.repository.lock_workspace(workspace_id)
            if (
                workspace is None
                or workspace.archived_at is not None
                or workspace.status == WorkspaceStatus.CLOSED
            ):
                raise ResourceNotFoundError(message="Workspace not found.")
            owner = await self.repository.get_membership(workspace_id=workspace_id, user_id=user.id)
            if owner is not None:
                owner = await self.repository.lock_membership(
                    workspace_id=workspace_id, membership_id=owner.id
                )
            if owner is None or owner.status != MembershipStatus.ACTIVE:
                raise ResourceNotFoundError(message="Workspace not found.")
            if owner.role != MembershipRole.OWNER:
                raise ForbiddenError(message="Only workspace owners can view invitations.")

            now = datetime.now(UTC)
            if actor.exp <= int(now.timestamp()) or not (
                await self.identity_repository.access_session_is_valid(
                    session_id=actor.sid,
                    user_id=user.id,
                    security_version=actor.security_version,
                    now=now,
                )
            ):
                raise AuthenticationRequiredError()

            invitations = await self.repository.list_invitations(
                workspace_id=workspace_id, limit=limit, after=after
            )
            result = [WorkspaceInvitationResponse.model_validate(row) for row in invitations]
        return result

    async def accept_invitation(
        self,
        command: WorkspaceInvitationAcceptRequest,
        *,
        actor: AccessTokenClaims,
    ) -> MembershipResponse:
        """Consume a matching invitation and grant membership atomically.

        Accept only claims validated by the authentication dependency. User locks
        precede workspace locks; workflows needing multiple users lock by UUID.
        """
        token_hash = hash_token(command.token.get_secret_value())
        invalidated = False
        result: MembershipResponse | None = None

        try:
            async with self.session.begin():
                candidate = await self.repository.get_invitation_by_token_hash(
                    token_hash=token_hash
                )
                if candidate is None:
                    await self._require_active_actor(actor)
                    raise InvalidInvitationError()

                invitation_id = candidate.id
                workspace_id = candidate.workspace_id
                inviter_id = candidate.invited_by_user_id

                # Reciprocal invitations must not acquire user locks in opposite orders.
                users: dict[UUID, User | None] = {}
                for user_id in sorted({actor.sub, inviter_id}):
                    users[user_id] = await self.identity_repository.lock_user(user_id)
                # The actor lock is already held; check current credentials.
                user = await self._require_active_actor(actor)
                inviter = users[inviter_id]

                workspace = await self.repository.lock_workspace(workspace_id)
                if (
                    workspace is None
                    or workspace.archived_at is not None
                    or workspace.status == WorkspaceStatus.CLOSED
                ):
                    raise InvalidInvitationError()

                inviter_membership = await self.repository.get_membership(
                    workspace_id=workspace_id, user_id=inviter_id
                )
                if inviter_membership is not None:
                    inviter_membership = await self.repository.lock_membership(
                        workspace_id=workspace_id, membership_id=inviter_membership.id
                    )
                membership = await self.repository.get_membership(
                    workspace_id=workspace_id, user_id=user.id
                )
                if membership is not None:
                    membership = await self.repository.lock_membership(
                        workspace_id=workspace_id, membership_id=membership.id
                    )
                invitation = await self.repository.lock_invitation(
                    workspace_id=workspace_id, invitation_id=invitation_id
                )

                now = datetime.now(UTC)
                if actor.exp <= int(now.timestamp()):
                    raise AuthenticationRequiredError()
                if not await self.identity_repository.access_session_is_valid(
                    session_id=actor.sid,
                    user_id=user.id,
                    security_version=actor.security_version,
                    now=now,
                ):
                    raise AuthenticationRequiredError()

                if (
                    invitation is None
                    or invitation.invited_by_user_id != inviter_id
                    or invitation.token_hash != token_hash
                    or invitation.normalized_email != user.normalized_email
                    or invitation.accepted_at is not None
                    or invitation.revoked_at is not None
                    or invitation.role not in {MembershipRole.MARKETER, MembershipRole.ANALYST}
                ):
                    raise InvalidInvitationError()

                if (
                    inviter is None
                    or inviter.status != UserStatus.ACTIVE
                    or inviter.email_verified_at is None
                    or inviter_membership is None
                    or inviter_membership.status != MembershipStatus.ACTIVE
                    or inviter_membership.role != MembershipRole.OWNER
                ):
                    await self.repository.revoke_invitation(
                        workspace_id=workspace_id, invitation_id=invitation_id, now=now
                    )
                    invalidated = True
                else:
                    if membership is not None:
                        if membership.status == MembershipStatus.ACTIVE:
                            raise ConflictError(
                                code="WORKSPACE_MEMBER_ALREADY_ACTIVE",
                                message="You are already an active workspace member.",
                            )
                        if membership.status == MembershipStatus.SUSPENDED:
                            raise ConflictError(
                                code="WORKSPACE_MEMBER_SUSPENDED",
                                message="An invitation cannot restore a suspended membership.",
                            )

                    consumed = await self.repository.consume_invitation(
                        workspace_id=workspace_id,
                        invitation_id=invitation_id,
                        token_hash=token_hash,
                        normalized_email=user.normalized_email,
                        accepted_by_user_id=user.id,
                        now=now,
                    )
                    if not consumed:
                        raise InvalidInvitationError()

                    if membership is None:
                        membership = await self.repository.create_membership(
                            workspace_id=workspace_id,
                            user_id=user.id,
                            role=invitation.role,
                            status=MembershipStatus.ACTIVE,
                            invited_by_user_id=inviter_id,
                            accepted_at=now,
                        )
                    else:
                        # Reuse the unique workspace/user row without retaining
                        # any old owner role. Legacy invited rows are supported.
                        membership.role = invitation.role
                        membership.status = MembershipStatus.ACTIVE
                        membership.invited_by_user_id = inviter_id
                        membership.accepted_at = now
                        membership.removed_at = None
                        await self.repository.flush()
                    await self.session.refresh(membership)
                    result = MembershipResponse.model_validate(membership)

            # Raising inside the transaction would undo permanent invalidation.
            if invalidated:
                raise InvalidInvitationError()
            if result is None:
                raise RuntimeError("Invitation acceptance completed without a membership")
            return result

        except StaleDataError:
            raise ConflictError(
                code="WORKSPACE_MEMBERSHIP_CHANGED",
                message="The membership changed. Reload and try again.",
            ) from None

    async def get_workspace(
        self, workspace_id: UUID, *, actor: AccessTokenClaims
    ) -> WorkspaceResponse:
        async with self.session.begin():
            user = await self._require_active_actor(actor)

            workspace = await self.repository.get_workspace_for_user(
                workspace_id=workspace_id, user_id=user.id
            )
            if workspace is None:
                raise ResourceNotFoundError(message="Workspace not Found")

            result = WorkspaceResponse.model_validate(workspace)
        return result

    async def list_workspace(
        self,
        *,
        actor: AccessTokenClaims,
        status: WorkspaceStatus | None = None,
        role: MembershipRole | None = None,
        search: str | None = None,
        limit: int = 50,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[WorkspaceResponse]:
        async with self.session.begin():
            user = await self._require_active_actor(actor)

            workspaces = await self.repository.list_workspaces_for_user(
                user_id=user.id, status=status, role=role, search=search, limit=limit, after=after
            )
            result = [WorkspaceResponse.model_validate(workspace) for workspace in workspaces]
            return result
