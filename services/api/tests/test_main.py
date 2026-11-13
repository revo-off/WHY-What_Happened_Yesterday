import os

# main.py lit DATABASE_URL à l'import : SQLite en mémoire, pas besoin de Postgres en CI.
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

import main  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_fails_when_database_is_down(client, monkeypatch):
    # Port 1 sur localhost : connexion refusée immédiatement.
    unreachable = create_engine("postgresql+psycopg2://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(main, "engine", unreachable)
    assert client.get("/health").status_code == 500


def test_metrics_exposed(client):
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
