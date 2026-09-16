from fastapi import APIRouter, status

router = APIRouter(tags=["health"])

@router.get(
    "/health/live",
    status_code=status.HTTP_200_OK,
    summary="check whether the API process is running"
)
async def liveness()-> dict[str, str]:
    return {"status":"ok"}


