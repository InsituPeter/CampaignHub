from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User, UserActionToken, UserActionTokenPurpose, UserSession


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_user(self, *, email: str, normalized_email: str, password_hash: str) -> User:
        user = User(email=email, normalized_email=normalized_email, password_hash=password_hash)

        self.session.add(user)
        await self.session.flush()
        return user

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_user_by_email(self, normalized_email: str) -> User | None:
        statement = select(User).where(User.normalized_email == normalized_email)
        return await self.session.scalar(statement)

    async def lock_user(self, user_id: UUID) -> User | None:
        statement = (
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def add_session(self, user_session: UserSession) -> UserSession:
        self.session.add(user_session)
        await self.session.flush()
        return user_session

    async def get_session_by_hash(self, token_hash: str) -> UserSession | None:
        statement = select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        return await self.session.scalar(statement)

    async def access_session_is_valid(
        self, *, session_id: UUID, user_id: UUID, security_version: int, now: datetime
    ) -> bool:
        # Rotation alone does not invalidate an existing access JWT.
        # Family revocation updates every retained session row.
        statement = select(UserSession.id).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.security_version == security_version,
            UserSession.issued_at <= now,
            UserSession.expires_at > now,
            UserSession.revoked_at.is_(None),
        )
        return await self.session.scalar(statement) is not None

    async def mark_session_rotated(self, *, session_id: UUID, user_id: UUID, now: datetime) -> bool:

        statement = (
            update(UserSession)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.issued_at <= now,
                UserSession.expires_at > now,
                UserSession.rotated_at.is_(None),
                UserSession.revoked_at.is_(None),
            )
            .values(rotated_at=now, last_used_at=now)
            .returning(UserSession.id)
            .execution_options(synchronize_session="fetch")
        )
        return await self.session.scalar(statement) is not None

    async def revoke_token_family(
        self, *, user_id: UUID, token_family_id: UUID, now: datetime, reason: str
    ) -> None:
        statement = (
            update(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.token_family_id == token_family_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=now, revocation_reason=reason)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.execute(statement)

    async def revoke_all_sessions(self, *, user_id: UUID, now: datetime, reason: str) -> None:
        statement = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now, revocation_reason=reason)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.execute(statement)

    async def add_action_token(self, token: UserActionToken) -> UserActionToken:
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_action_token_by_hash(
        self, *, token_hash: str, purpose: UserActionTokenPurpose
    ) -> UserActionToken | None:
        statement = select(UserActionToken).where(
            UserActionToken.token_hash == token_hash, UserActionToken.purpose == purpose
        )
        return await self.session.scalar(statement)

    async def consume_action_token(
        self,
        *,
        token_id: UUID,
        user_id: UUID,
        purpose: UserActionTokenPurpose,
        now: datetime,
    ) -> bool:
        statement = (
            update(UserActionToken)
            .where(
                UserActionToken.id == token_id,
                UserActionToken.user_id == user_id,
                UserActionToken.purpose == purpose,
                UserActionToken.created_at <= now,
                UserActionToken.expires_at > now,
                UserActionToken.consumed_at.is_(None),
                UserActionToken.revoked_at.is_(None),
            )
            .values(consumed_at=now, updated_at=now)
            .returning(UserActionToken.id)
            .execution_options(synchronize_session="fetch")
        )
        return await self.session.scalar(statement) is not None

    async def revoke_open_action_tokens(
        self,
        *,
        user_id: UUID,
        purpose: UserActionTokenPurpose,
        now: datetime,
    ) -> None:
        # Includes expired tokens because they still occupy the partial
        # unique index until consumed or revoked.
        statement = (
            update(UserActionToken)
            .where(
                UserActionToken.user_id == user_id,
                UserActionToken.purpose == purpose,
                UserActionToken.consumed_at.is_(None),
                UserActionToken.revoked_at.is_(None),
            )
            .values(revoked_at=now, updated_at=now)
            .execution_options(synchronize_session="fetch")
        )
        await self.session.execute(statement)
