# Phase 0 — Foundation hardening

**Goal:** make the v0.1.0 rate limiter correct, testable and safe to run anywhere before anything is built on top of it.

## What changed and why

| Problem in v0.1.0 | Fix | Where |
|---|---|---|
| Lua file opened with a path relative to the *current directory*, so starting the app from another folder crashed it | Resolve the path from the module's own location with `Path(__file__).parent` | `src/gateway/core/rate_limiter.py` |
| Redis host hardcoded to `localhost` | Settings read from environment variables / `.env` | `src/gateway/config.py` |
| Synchronous Redis call inside an `async def` endpoint blocked the event loop | `redis.asyncio` client, `await`ed | `src/gateway/api/main.py` |
| Redis client created at *import time* | Created in FastAPI's `lifespan` and closed on shutdown | `main.py` → `lifespan()` |
| Redis down → 55 s hang, then a 500 error | 0.5 s timeouts plus a handler that returns `503` (**fail closed**) | `main.py` → `redis_unavailable()` |
| Timestamp from the app server's clock | Redis's own `TIME` inside the script | `core/lua/token_bucket.lua` |
| Deprecated `HMSET`, fixed 1-hour expiry | `HSET`; expiry = time to refill completely | `token_bucket.lua` |
| 429 didn't say when to retry | Script returns the wait, sent as the `Retry-After` header | `token_bucket.lua`, `main.py` |
| No rate-limit tests | Exhaustion, refill, per-user buckets, Retry-After, Redis-down tests | `tests/test_api.py` |
| `torch` + `transformers` (GBs) installed for an echo API | Moved to an optional `ml` group: `uv sync --group ml` | `pyproject.toml` |
| Tests only ran on your machine | GitHub Actions CI with a Redis service container | `.github/workflows/ci.yml` |

## The request, step by step

1. **Startup:** `lifespan()` creates one Redis connection pool and registers the Lua script. Registering computes the script's SHA1 hash, so later calls send `EVALSHA <sha>` instead of the whole script.
2. **`POST /chat`:** FastAPI parses the JSON into `ChatRequest` (Pydantic), which returns 422 if a field is missing.
3. `take_token()` awaits the script. While it waits on the network, the event loop serves other requests. That's the whole point of `async`.
4. **Inside Redis, atomically:** read the bucket → add `elapsed × refill_rate` tokens (capped at capacity) → spend one if possible → save → set expiry.
5. The script returns `[1, 0]` (allowed) or `[0, ms_to_wait]`, which becomes 200 or 429 + `Retry-After`.
6. If Redis raises anything (`RedisError`), the exception handler turns it into 503.

## Prerequisites to study (in this order)

1. **Docker basics.** Image vs container, `docker compose up -d`, port mapping (`6379:6379`). [Docker: Get started](https://docs.docker.com/get-started/)
2. **Redis basics.** Try these in `docker compose exec redis redis-cli`: `SET`/`GET`, `HSET`/`HGETALL`, `EXPIRE`/`TTL`, `KEYS ratelimit:*`. [Redis data types](https://redis.io/docs/latest/develop/data-types/)
3. **Lua scripting in Redis.** `EVAL` vs `EVALSHA`, and why scripts are atomic. [Scripting with Lua](https://redis.io/docs/latest/develop/programmability/eval-intro/)
4. **Token bucket vs fixed window vs sliding window.** Search "rate limiting algorithms token bucket"; the Stripe engineering blog post on rate limiters is a good one.
5. **asyncio.** Event loop, coroutines, why one blocking call freezes every request. [asyncio docs](https://docs.python.org/3/library/asyncio.html)
6. **FastAPI lifespan + exception handlers.** [Lifespan events](https://fastapi.tiangolo.com/advanced/events/), [Handling errors](https://fastapi.tiangolo.com/tutorial/handling-errors/)
7. **Fail open vs fail closed.** A security/availability trade-off; read ADR-001.
8. **pytest fixtures + monkeypatch.** [Fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html), [monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)
9. **GitHub Actions service containers.** [About service containers](https://docs.github.com/en/actions/use-cases-and-examples/using-containerized-services/about-service-containers)

## Try it yourself

```bash
docker compose up -d
uv run uvicorn gateway.api.main:app --reload
# 11 quick requests → the 11th returns 429 with Retry-After
for i in $(seq 11); do curl -s -o /dev/null -w "%{http_code} " -X POST localhost:8000/chat -H "Content-Type: application/json" -d '{"user_id":"me","message":"hi"}'; done
docker compose exec redis redis-cli HGETALL ratelimit:me   # see the bucket
docker compose stop redis   # now /chat returns 503 within about half a second
```

## Review checklist — can you explain…

- [ ] Why `Path(__file__).parent` fixes the crash, and what `__file__` is?
- [ ] What would happen to other requests if `take_token` used the sync Redis client?
- [ ] Why the bucket logic is in Lua instead of Python `GET` → compute → `SET`? (Hint: two requests at once.)
- [ ] Why the script uses `redis.call('TIME')` instead of a timestamp passed in?
- [ ] How `retry_ms = (1 - tokens) / refill_rate * 1000` is derived?
- [ ] Why we fail closed, and one situation where failing open would be the better choice?
- [ ] How `conftest.py` makes tests use Redis DB 15, and why the env var must be set *before* importing the app?
- [ ] How `monkeypatch.setattr(config, ...)` changes the capacity for one test only?

## Interview angle

"How would you rate-limit an API running on many servers?" You can now answer from experience: a shared store, an atomic token bucket, a clock the servers agree on, the fail-closed trade-off, and how you tested it.
