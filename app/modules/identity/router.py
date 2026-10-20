from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import get_access_token_service
from app.core.security import AccessTokenClaims, AccessTokenService
from app.modules.identity.dependencies import get_current_identity
from app.modules.identity.http_dependencies import (
    IdentityDelivery,
    get_identity_delivery,
    get_identity_service,
    protect_identity_route,
)
from app.modules.identity.schemas import (
    EmailVerificationConfirmRequest,
    EmailVerificationRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.modules.identity.service import (
    ActionTokenDelivery,
    EmailAlreadyRegisteredError,
    IdentityService,
)

router = APIRouter(
    prefix="/auth", tags=["Authentication"], dependencies=[Depends(protect_identity_route)]
)
Service = Annotated[IdentityService, Depends(get_identity_service)]
Tokens = Annotated[AccessTokenService, Depends(get_access_token_service)]
Delivery = Annotated[IdentityDelivery, Depends(get_identity_delivery)]
Identity = Annotated[AccessTokenClaims, Depends(get_current_identity)]
VERIFICATION_MESSAGE = (
    "If the account is eligible, you will receive an email with verification instructions."
)
RESET_MESSAGE = (
    "If the account is eligible, you will receive an email with password-reset instructions."
)


@router.post("/register", response_model=MessageResponse, status_code=202)
async def register(
    command: RegisterRequest, service: Service, delivery: Delivery
) -> MessageResponse:
    prepared: ActionTokenDelivery | None = None
    try:
        result = await service.register(command)
        prepared = ActionTokenDelivery(
            email=str(result.user.email),
            token=result.verification_token,
            expires_at=result.verification_expires_at,
        )
    except EmailAlreadyRegisteredError:
        pass
    await delivery.enqueue("email_verification", prepared)
    return MessageResponse(message=VERIFICATION_MESSAGE)


@router.post("/login", response_model=TokenResponse)
async def login(command: LoginRequest, service: Service, tokens: Tokens) -> TokenResponse:
    return await service.login(command, access_token_service=tokens)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(command: RefreshTokenRequest, service: Service, tokens: Tokens) -> TokenResponse:
    return await service.refresh_tokens(command, access_token_service=tokens)


@router.post("/logout", response_model=MessageResponse)
async def logout(command: LogoutRequest, service: Service) -> MessageResponse:
    await service.logout(command)
    return MessageResponse(message="Logged out.")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(identity: Identity, service: Service) -> MessageResponse:
    await service.logout_all(identity)
    return MessageResponse(message="Logged out of all sessions.")


@router.post("/email-verifications", response_model=MessageResponse, status_code=202)
async def request_verification(
    command: EmailVerificationRequest, service: Service, delivery: Delivery
) -> MessageResponse:
    prepared = await service.request_email_verification(command)
    await delivery.enqueue("email_verification", prepared)
    return MessageResponse(message=VERIFICATION_MESSAGE)


@router.post("/email-verifications/confirm", response_model=UserResponse)
async def verify_email(command: EmailVerificationConfirmRequest, service: Service) -> UserResponse:
    return await service.verify_email(command)


@router.post("/password-resets", response_model=MessageResponse, status_code=202)
async def request_reset(
    command: PasswordResetRequest, service: Service, delivery: Delivery
) -> MessageResponse:
    prepared = await service.request_password_reset(command)
    await delivery.enqueue("password_reset", prepared)
    return MessageResponse(message=RESET_MESSAGE)


@router.post("/password-resets/confirm", response_model=MessageResponse)
async def reset_password(command: PasswordResetConfirmRequest, service: Service) -> MessageResponse:
    await service.reset_password(command)
    return MessageResponse(message="Your password has been reset. Please log in again.")


@router.get("/me", response_model=UserResponse)
async def me(identity: Identity, service: Service) -> UserResponse:
    return await service.get_me(identity)
