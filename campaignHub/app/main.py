from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    application = FastAPI(
        title=f"{app_settings.app_name} API",
        version="0.1.0",
        description="Nigeria-first omnichannel marketing automation API",
        debug=app_settings.debug,
    )

    application.state.settings = app_settings
    application.include_router(health_router)

    return application


app = create_app()
