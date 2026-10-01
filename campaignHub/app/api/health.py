from typing import cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Database

router = APIRouter(
    prefix="/health",
    tags=["health"],
)


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "A required service is unavailable"}
    },
)
async def readiness(request: Request) -> JSONResponse:
    database = cast(Database, request.app.state.database)

    try:
        await database.ping()
    except SQLAlchemyError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "checks": {"database": "unavailable"}},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ready",
            "checks": {
                "database": "ready",
            },
        },
    )
