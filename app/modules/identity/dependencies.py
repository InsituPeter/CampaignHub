from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_access_token_service, get_session
from app.core.exceptions import AuthenticationRequiredError
from app.core.security import AccessTokenClaims, AccessTokenService
from app.modules.identity.service import IdentityService

bearer = HTTPBearer(auto_error=False)


async def get_current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    access_token_service: Annotated[AccessTokenService, Depends(get_access_token_service)],
) -> AccessTokenClaims:
    """Attach to every protected endpoint; JWT signature checks alone are insufficient."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationRequiredError()
    return await IdentityService(session).authenticate_access_token(
        credentials.credentials, access_token_service=access_token_service
    )
