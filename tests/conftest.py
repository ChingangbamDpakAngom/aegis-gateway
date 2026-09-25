import os

# Tests use their own Redis database so they never touch dev data. Set before the app is imported.
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")

import httpx2
import pytest
from fastapi.testclient import TestClient

from gateway.api.main import app
from gateway.keys import create_key


def fake_ollama(request: httpx2.Request) -> httpx2.Response:
    """Stands in for Ollama's /api/chat so tests need no model (and CI no GPU)."""
    return httpx2.Response(200, json={
        "model": "llama3.2",
        "message": {"role": "assistant", "content": "Hello from the fake model"},
        "prompt_eval_count": 11,
        "eval_count": 5,
    })


def use_model(handler):
    """Point the app's model client at `handler` (a function Request -> Response)."""
    app.state.http = httpx2.AsyncClient(base_url="http://ollama", transport=httpx2.MockTransport(handler))


@pytest.fixture
def client():
    # The `with` block runs the app's lifespan, which opens the Redis connection.
    with TestClient(app) as c:
        c.portal.call(app.state.redis.flushdb)
        use_model(fake_ollama)
        yield c


@pytest.fixture
def new_key(client):
    """Create a real API key in the test Redis: new_key("alice", capacity=2)."""

    def make(client_id="test-client", capacity=None, refill_per_s=None):
        return client.portal.call(create_key, app.state.redis, client_id, capacity, refill_per_s)

    return make
