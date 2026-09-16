from fastapi.testclient import TestClient

def test_liveness_returns_Ok(client:TestClient)-> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
