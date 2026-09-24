"""Create API keys.

    uv run python -m gateway.keys <client_id> [--capacity N] [--refill-per-s R]

The key is printed once and never stored: Redis only keeps its SHA-256 hash.
Keys created for the same client_id share one rate-limit bucket, which lets a
client rotate keys without getting a fresh allowance.
"""

import argparse
import asyncio
import hashlib
import secrets

from redis.asyncio import Redis

from gateway import config


def key_hash(api_key: str) -> str:
    # A plain (unsalted) SHA-256 is enough here: keys are 256 random bits, so there is
    # nothing to brute-force. Passwords, being guessable, would need bcrypt/argon2.
    return hashlib.sha256(api_key.encode()).hexdigest()


async def create_key(
    redis: Redis, client_id: str, capacity: int | None = None, refill_per_s: float | None = None
) -> str:
    api_key = "aeg_" + secrets.token_urlsafe(32)
    record: dict[str, str | int | float] = {"client_id": client_id}
    if capacity is not None:
        record["capacity"] = capacity
    if refill_per_s is not None:
        record["refill_per_s"] = refill_per_s
    await redis.hset(f"apikey:{key_hash(api_key)}", mapping=record)
    return api_key


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Create an Aegis API key")
    parser.add_argument("client_id")
    parser.add_argument("--capacity", type=int)
    parser.add_argument("--refill-per-s", type=float)
    args = parser.parse_args()

    redis = Redis.from_url(config.REDIS_URL)
    try:
        print(await create_key(redis, args.client_id, args.capacity, args.refill_per_s))
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
