import time

from gateway import config


def chat(client, user_id="test-user", message="Hello from pytest"):
    return client.post("/chat", json={"user_id": user_id, "message": message})


def test_health_check(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


def test_chat_request(client):
    response = chat(client)

    assert response.status_code == 200
    assert response.json() == {"echo": "Hello from pytest", "from": "test-user"}


def test_chat_validation(client):
    response = client.post("/chat", json={"user_id": "test-user"})

    assert response.status_code == 422


def test_bucket_exhausts_then_429(client, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_CAPACITY", 3)

    statuses = [chat(client).status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]


def test_429_says_when_to_retry(client, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_CAPACITY", 1)
    chat(client)

    response = chat(client)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"


def test_tokens_refill_over_time(client, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_CAPACITY", 1)
    monkeypatch.setattr(config, "RATE_LIMIT_REFILL_PER_S", 20.0)  # one token every 50 ms
    assert chat(client).status_code == 200
    assert chat(client).status_code == 429

    time.sleep(0.1)

    assert chat(client).status_code == 200


def test_each_user_has_own_bucket(client, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_CAPACITY", 1)
    chat(client, user_id="alice")

    assert chat(client, user_id="alice").status_code == 429
    assert chat(client, user_id="bob").status_code == 200


def test_redis_down_fails_closed(monkeypatch):
    from fastapi.testclient import TestClient

    from gateway.api.main import app

    monkeypatch.setattr(config, "REDIS_URL", "redis://localhost:1/0")  # nothing listens here
    with TestClient(app) as client:
        response = chat(client)

    assert response.status_code == 503
