from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import ConflictError
from app.core.middleware import REQUEST_ID_HEADER
from app.main import create_app


@pytest.fixture
def error_client() -> Iterator[TestClient]:
    application: FastAPI = create_app()

    @application.get("/test/app-error")
    async def raise_app_error() -> None:
        raise ConflictError(
            code="CAMPAIGN_ALREADY_RUNNING",
            message="The campaign is already running.",
            details=[
                {
                    "field": "campaign_id",
                    "reason": "invalid_state",
                }
            ],
        )

    @application.get("/test/validation")
    async def validate_quantity(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    @application.get("/test/unexpected")
    async def raise_unexpected_error() -> None:
        raise RuntimeError("Internal implementation detail")

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        yield client


def test_app_error_uses_standard_error_contract(
    error_client: TestClient,
) -> None:
    request_id = "known-error-request"

    response = error_client.get(
        "/test/app-error",
        headers={REQUEST_ID_HEADER: request_id},
    )

    assert response.status_code == 409
    assert response.headers[REQUEST_ID_HEADER] == request_id
    assert response.json() == {
        "error": {
            "code": "CAMPAIGN_ALREADY_RUNNING",
            "message": "The campaign is already running.",
            "status": 409,
            "request_id": request_id,
            "details": [
                {
                    "field": "campaign_id",
                    "reason": "invalid_state",
                }
            ],
            "retryable": False,
        }
    }


def test_validation_error_uses_standard_error_contract(
    error_client: TestClient,
) -> None:
    request_id = "validation-request"

    response = error_client.get(
        "/test/validation",
        params={"quantity": "not-an-integer"},
        headers={REQUEST_ID_HEADER: request_id},
    )

    response_body = response.json()
    error = response_body["error"]

    assert response.status_code == 422
    assert response.headers[REQUEST_ID_HEADER] == request_id
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "The request contains invalid data."
    assert error["status"] == 422
    assert error["request_id"] == request_id
    assert error["retryable"] is False
    assert error["details"]
    assert error["details"][0]["field"] == "query.quantity"
    assert error["details"][0]["type"] == "int_parsing"


def test_unexpected_error_hides_internal_details(
    error_client: TestClient,
) -> None:
    request_id = "unexpected-error-request"

    response = error_client.get(
        "/test/unexpected",
        headers={REQUEST_ID_HEADER: request_id},
    )

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == request_id
    assert response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred.",
            "status": 500,
            "request_id": request_id,
            "details": [],
            "retryable": False,
        }
    }
    assert "Internal implementation detail" not in response.text
