"""Runtime settings, read from environment variables (or a local .env file)."""

import os

from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Fail fast: a slow Redis should turn into a 503, not a hung request.
REDIS_TIMEOUT_S = float(os.getenv("REDIS_TIMEOUT_S", "0.5"))

RATE_LIMIT_CAPACITY = int(os.getenv("RATE_LIMIT_CAPACITY", "10"))
RATE_LIMIT_REFILL_PER_S = float(os.getenv("RATE_LIMIT_REFILL_PER_S", "1.0"))
