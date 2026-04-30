"""Tests for the health check endpoint.

This file shows how to test a FastAPI endpoint.

Key concepts:
  - TestClient simulates HTTP requests without starting a real server.
  - Each test function starts with "test_" so pytest can find it.
  - We check status code AND response body to make sure everything works.
"""

from fastapi.testclient import TestClient

from src.app.main import app

client = TestClient(app)


def test_health_returns_200() -> None:
    """GET /health should return 200 OK."""
    response = client.get("/health")

    assert response.status_code == 200


def test_health_returns_status_and_version() -> None:
    """GET /health should return a JSON body with status and version."""
    response = client.get("/health")
    data = response.json()

    assert data["status"] == "healthy"
    assert "version" in data


def test_health_service_directly() -> None:
    """You can also test the service without HTTP, calling it directly."""
    from src.app.services.health import HealthService

    service = HealthService()
    result = service.check()

    assert result.status == "healthy"
    assert result.version == "0.1.0"
