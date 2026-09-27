from fastapi.testclient import TestClient


def test_health_reports_service_is_healthy(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "cortex-api",
        "version": "1.0.0-alpha",
    }
