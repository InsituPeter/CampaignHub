import re

from fastapi.testclient import TestClient

from app.core.middleware import REQUEST_ID_HEADER

GENERATED_REQUEST_ID_PATTERN = re.compile(r"^req_[0-9a-f]{32}$")


def test_response_contains_generated_request_id(client: TestClient) -> None:
    response = client.get("/health/live")
    request_id = response.headers[REQUEST_ID_HEADER]

    assert response.status_code == 200
    assert GENERATED_REQUEST_ID_PATTERN.fullmatch(request_id) is not None


def test_valid_request_id_is_preserved(client: TestClient) -> None:
    supplied_request_id = "client-request-123"
    response = client.get(
        "/health/live",
        headers={REQUEST_ID_HEADER: supplied_request_id},
    )
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == supplied_request_id


def test_invalid_client_request_id_is_replaced(client: TestClient) -> None:
    invalid_request_id = "invalid request-id"
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: invalid_request_id})
    returned_request_id = response.headers[REQUEST_ID_HEADER]

    assert response.status_code == 200
    assert returned_request_id != invalid_request_id
    assert GENERATED_REQUEST_ID_PATTERN.fullmatch(returned_request_id) is not None


def test_request_id_does_not_leak_between_requests(client: TestClient) -> None:
    supplied_request_id = "first-request-124"
    first_response = client.get("/health/live", headers={REQUEST_ID_HEADER: supplied_request_id})
    second_response = client.get("/health/live")

    first_request_id = first_response.headers[REQUEST_ID_HEADER]
    second_request_id = second_response.headers[REQUEST_ID_HEADER]

    assert first_request_id == supplied_request_id
    assert second_request_id != first_request_id
    assert GENERATED_REQUEST_ID_PATTERN.fullmatch(second_request_id) is not None
