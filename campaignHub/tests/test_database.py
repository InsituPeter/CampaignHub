from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import Database


def create_test_database() -> Database:
    settings = Settings(_env_file=None)
    return Database(settings)


async def test_session_provides_async_session() -> None:
    database = create_test_database()

    try:
        async with database.session() as session:
            assert isinstance(session, AsyncSession)
    finally:
        await database.dispose()


async def test_transaction_provides_active_transaction() -> None:
    database = create_test_database()

    try:
        async with database.transaction() as session:
            assert isinstance(session, AsyncSession)
            assert session.in_transaction()

    finally:
        await database.dispose()
