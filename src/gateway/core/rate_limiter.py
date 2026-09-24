from pathlib import Path

from redis.commands.core import AsyncScript

# Resolved relative to this file, so it loads no matter where the app is started from.
TOKEN_BUCKET_LUA = (Path(__file__).parent / "lua" / "token_bucket.lua").read_text()


async def take_token(
    script: AsyncScript, key: str, capacity: int, refill_per_s: float
) -> tuple[bool, float]:
    """Spend one token from `key`'s bucket. Returns (allowed, seconds until a token is free)."""
    allowed, retry_ms = await script(keys=[f"ratelimit:{key}"], args=[capacity, refill_per_s])
    return allowed == 1, retry_ms / 1000
