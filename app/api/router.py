from fastapi import APIRouter

from app.modules.identity.router import router as identity_router
from app.modules.workspace.invitation_router import router as invitation_router
from app.modules.workspace.router import router as workspace_router

api_router = APIRouter()
api_router.include_router(identity_router)
api_router.include_router(workspace_router)
api_router.include_router(invitation_router)
