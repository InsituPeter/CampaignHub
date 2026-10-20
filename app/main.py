import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    configure_logging(
        log_level=app_settings.log_level,
        environment=app_settings.environment,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(app_settings)
        application.state.database = database

        logger.info("CampaignHub application started")

        try:
            yield
        finally:
            await database.dispose()
            logger.info("CampaignHub application stopped")

    application = FastAPI(
        title=f"{app_settings.app_name} API",
        version="0.1.0",
        description="Nigeria-first omnichannel marketing automation API",
        debug=app_settings.debug,
        lifespan=lifespan,
    )

    application.state.settings = app_settings

    application.add_middleware(RequestIdMiddleware)
    register_error_handlers(application)

    application.include_router(health_router)
    application.include_router(
        api_router,
        prefix=app_settings.api_v1_prefix,
    )

    return application


app = create_app()
