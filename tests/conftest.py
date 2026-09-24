import os

# Tests use their own Redis database so they never touch dev data. Set before the app is imported.
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")

import pytest
from fastapi.testclient import TestClient

from gateway.api.main import app


@pytest.fixture
def client():
    # The `with` block runs the app's lifespan, which opens the Redis connection.
    with TestClient(app) as c:
        c.portal.call(app.state.redis.flushdb)
        yield c
