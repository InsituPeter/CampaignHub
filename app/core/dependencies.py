from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import Database
from app.core.security import AccessTokenService


def get_access_token_service(request: Request) -> AccessTokenService:
    settings = cast(Settings, request.app.state.settings)
    return AccessTokenService.from_settings(settings)


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session
