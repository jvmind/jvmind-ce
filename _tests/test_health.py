"""Tests for health endpoints."""


def test_health_endpoint(client):
    """Test health endpoint returns ok status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "users_count" in data
    assert "agents_count" in data
