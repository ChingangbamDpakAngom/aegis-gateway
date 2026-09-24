import time

from gateway import config
from gateway.keys import key_hash


def chat(client, key, message="Hello from pytest"):
    return client.post("/chat", headers={"X-API-Key": key}, json={"message": message})


def error_code(response):
    return response.json()["error"]["code"]


def test_health_check(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "OK"}


# --- Identity ---


def test_chat_with_valid_key(client, new_key):
    response = chat(client, new_key("alice"))

    assert response.status_code == 200
    assert response.json() == {"echo": "Hello from pytest", "client_id": "alice"}


def test_missing_key_is_401(client):
    response = client.post("/chat", json={"message": "hi"})

    assert response.status_code == 401
    assert error_code(response) == "missing_api_key"


def test_unknown_key_is_401(client, new_key):
    new_key()

    response = chat(client, "aeg_not-a-real-key")

    assert response.status_code == 401
    assert error_code(response) == "invalid_api_key"


def test_keys_are_stored_hashed(client, new_key):
    key = new_key()
    keys_in_redis = client.portal.call(client.app.state.redis.keys, "*")

    assert f"apikey:{key_hash(key)}" in keys_in_redis
    assert not any(key in k for k in keys_in_redis)


# --- Request contract ---


def test_missing_message_is_422(client, new_key):
    response = client.post("/chat", headers={"X-API-Key": new_key()}, json={})

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert response.json()["error"]["details"][0]["field"] == "message"


def test_empty_message_is_422(client, new_key):
    assert chat(client, new_key(), message="").status_code == 422


def test_oversized_message_is_422(client, new_key):
    too_long = "x" * (config.MAX_MESSAGE_CHARS + 1)

    assert chat(client, new_key(), message=too_long).status_code == 422


def test_unknown_field_is_422(client, new_key):
    # The old v0.1 body sent user_id; identity now comes only from the key.
    response = client.post(
        "/chat", headers={"X-API-Key": new_key()}, json={"message": "hi", "user_id": "bob"}
    )

    assert response.status_code == 422


def test_unknown_route_uses_error_format(client):
    response = client.get("/nope")

    assert response.status_code == 404
    assert "code" in response.json()["error"]


# --- Rate limiting (per client, per-key limits) ---


def test_bucket_exhausts_then_429(client, new_key):
    key = new_key(capacity=3)

    statuses = [chat(client, key).status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]


def test_default_limit_applies_without_override(client, new_key, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_CAPACITY", 2)
    key = new_key()

    statuses = [chat(client, key).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


def test_429_says_when_to_retry(client, new_key):
    key = new_key(capacity=1)
    chat(client, key)

    response = chat(client, key)

    assert response.status_code == 429
    assert error_code(response) == "rate_limited"
    assert response.headers["Retry-After"] == "1"


def test_tokens_refill_over_time(client, new_key):
    key = new_key(capacity=1, refill_per_s=20.0)  # one token every 50 ms
    assert chat(client, key).status_code == 200
    assert chat(client, key).status_code == 429

    time.sleep(0.1)

    assert chat(client, key).status_code == 200


def test_each_client_has_own_bucket(client, new_key):
    alice, bob = new_key("alice", capacity=1), new_key("bob", capacity=1)
    chat(client, alice)

    assert chat(client, alice).status_code == 429
    assert chat(client, bob).status_code == 200


def test_keys_of_one_client_share_a_bucket(client, new_key):
    old_key, rotated_key = new_key("alice", capacity=1), new_key("alice", capacity=1)
    chat(client, old_key)

    assert chat(client, rotated_key).status_code == 429


# --- Failure policy ---


def test_redis_down_fails_closed(monkeypatch):
    from fastapi.testclient import TestClient

    from gateway.api.main import app

    monkeypatch.setattr(config, "REDIS_URL", "redis://localhost:1/0")  # nothing listens here
    with TestClient(app) as client:
        response = chat(client, "aeg_any-key")

    assert response.status_code == 503
    assert error_code(response) == "backend_unavailable"
