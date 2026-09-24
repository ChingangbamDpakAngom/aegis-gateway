import os

# Tests use their own Redis database so they never touch dev data. Set before the app is imported.
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")

import pytest
from fastapi.testclient import TestClient

from gateway.api.main import app
from gateway.keys import create_key


@pytest.fixture
def client():
    # The `with` block runs the app's lifespan, which opens the Redis connection.
    with TestClient(app) as c:
        c.portal.call(app.state.redis.flushdb)
        yield c


@pytest.fixture
def new_key(client):
    """Create a real API key in the test Redis: new_key("alice", capacity=2)."""

    def make(client_id="test-client", capacity=None, refill_per_s=None):
        return client.portal.call(create_key, app.state.redis, client_id, capacity, refill_per_s)

    return make
