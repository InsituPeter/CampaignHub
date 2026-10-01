from typing import cast
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError


def test_liveness_returns_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_ready_when_database_is_available(client: TestClient) -> None:
    application = cast(FastAPI, client.app)
    application.state.database.ping = AsyncMock()

    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": "ready",
        },
    }


def test_readiness_returns_unavailable_when_database_fails(client: TestClient) -> None:
    application = cast(FastAPI, client.app)
    application.state.database.ping = AsyncMock(side_effect=SQLAlchemyError("database unavailable"))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {
            "database": "unavailable",
        },
    }
