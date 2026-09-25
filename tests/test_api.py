import json
import os
import time

import httpx2
import pytest

from gateway import config
from gateway.keys import key_hash

from .conftest import use_model


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
    assert response.json() == {
        "reply": "Hello from the fake model",
        "model": config.MODEL,
        "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        "client_id": "alice",
    }


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


# --- Observability ---


def test_ready_when_redis_is_up(client):
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_not_ready_when_redis_is_down(monkeypatch):
    from fastapi.testclient import TestClient

    from gateway.api.main import app

    monkeypatch.setattr(config, "REDIS_URL", "redis://localhost:1/0")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200  # alive...
        response = client.get("/ready")  # ...but not able to serve

    assert response.status_code == 503
    assert error_code(response) == "backend_unavailable"


def test_every_response_has_a_request_id(client):
    first, second = client.get("/health"), client.get("/nope")

    assert len(first.headers["X-Request-ID"]) == 32
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_safe_incoming_request_id_is_reused(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


def test_unsafe_incoming_request_id_is_replaced(client):
    response = client.get("/health", headers={"X-Request-ID": 'x"}, "status": 200'})

    assert response.headers["X-Request-ID"] != 'x"}, "status": 200'


def test_one_json_log_line_per_request(client, new_key, caplog):
    key = new_key("alice", capacity=1)
    chat(client, key)
    caplog.clear()

    response = chat(client, key)  # rate limited

    [record] = [r for r in caplog.records if r.name == "aegis"]
    line = json.loads(record.getMessage())
    assert line["request_id"] == response.headers["X-Request-ID"]
    assert line["route"] == "/chat"
    assert line["status"] == 429
    assert line["error_code"] == "rate_limited"
    assert line["client_id"] == "alice"
    assert key not in record.getMessage()


def test_metrics_count_requests_by_route_and_status(client):
    from prometheus_client import REGISTRY

    def count():
        labels = {"route": "unmatched", "status": "404"}
        return REGISTRY.get_sample_value("aegis_requests_total", labels) or 0

    before = count()
    client.get("/scanner/probe/1")
    client.get("/scanner/probe/2")

    assert count() == before + 2
    body = client.get("/metrics").text
    assert "aegis_request_duration_seconds_bucket" in body
    assert "/scanner/probe" not in body  # raw paths never become labels


# --- Model backend ---


def test_model_request_is_capped_and_not_streamed(client, new_key):
    sent = []

    def ollama(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"message": {"content": "ok"}, "eval_count": 1})

    use_model(ollama)
    chat(client, new_key(), message="What is Redis?")

    [body] = sent
    assert body["messages"] == [{"role": "user", "content": "What is Redis?"}]
    assert body["stream"] is False
    assert body["options"]["num_predict"] == config.MAX_OUTPUT_TOKENS


def test_rate_limited_requests_never_reach_the_model(client, new_key):
    calls = []
    use_model(lambda request: calls.append(1) or httpx2.Response(200, json={"message": {"content": "ok"}}))
    key = new_key(capacity=1)

    chat(client, key)
    chat(client, key)

    assert len(calls) == 1  # the 429 cost nothing


def test_model_down_is_502(client, new_key):
    def refused(request):
        raise httpx2.ConnectError("connection refused")

    use_model(refused)
    response = chat(client, new_key())

    assert response.status_code == 502
    assert error_code(response) == "model_unavailable"


def test_model_error_status_is_502(client, new_key):
    use_model(lambda request: httpx2.Response(404, json={"error": "model not found"}))

    assert chat(client, new_key()).status_code == 502


def test_model_timeout_is_504(client, new_key):
    def slow(request):
        raise httpx2.ReadTimeout("too slow")

    use_model(slow)
    response = chat(client, new_key())

    assert response.status_code == 504
    assert error_code(response) == "model_timeout"


def test_tokens_are_counted_and_logged(client, new_key, caplog):
    from prometheus_client import REGISTRY

    def completion_tokens():
        labels = {"model": config.MODEL, "kind": "completion"}
        return REGISTRY.get_sample_value("aegis_llm_tokens_total", labels) or 0

    before = completion_tokens()
    chat(client, new_key())

    assert completion_tokens() == before + 5
    [record] = [r for r in caplog.records if r.name == "aegis"]
    assert json.loads(record.getMessage())["usage"] == {"prompt_tokens": 11, "completion_tokens": 5}


@pytest.mark.skipif(not os.getenv("AEGIS_LIVE_MODEL"), reason="set AEGIS_LIVE_MODEL=1 with Ollama running")
def test_live_model_answers():
    from fastapi.testclient import TestClient

    from gateway.api.main import app
    from gateway.keys import create_key

    with TestClient(app) as live:  # real Ollama client from the lifespan, not the fake
        key = live.portal.call(create_key, app.state.redis, "live-test", None, None)
        response = chat(live, key, message="Reply with one word: hello")

    assert response.status_code == 200, response.text
    assert response.json()["reply"].strip()
    assert response.json()["usage"]["completion_tokens"] > 0
