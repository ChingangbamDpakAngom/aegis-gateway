import time
import redis

redis_client = redis.Redis(host = 'localhost', port = 6379, decode_responses=True)

with open("src/gateway/core/lua/token_bucket.lua", "r") as f:
    rate_limit_script = redis_client.register_script(f.read())

def is_allowed(user_id: str, capacity: int = 10, refill_rate: float = 1.0 ) -> bool:
    result = rate_limit_script(
        keys = [f"ratelimit:{user_id}"],
        args = [capacity, refill_rate, time.time()]
    )
    return result == 1