from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        database_url = settings.database_url.get_secret_value()

        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=settings.database_echo,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            connect_args={"server_settings": {"application_name": settings.app_name}},
        )

        self.session_factory = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, autoflush=False, expire_on_commit=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session, session.begin():
            yield session

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
