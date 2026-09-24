import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from gateway import config
from gateway.api.schemas import ChatRequest
from gateway.core.rate_limiter import TOKEN_BUCKET_LUA, take_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One connection pool for the whole process, opened at startup and closed at shutdown.
    app.state.redis = Redis.from_url(
        config.REDIS_URL,
        decode_responses=True,
        socket_timeout=config.REDIS_TIMEOUT_S,
        socket_connect_timeout=config.REDIS_TIMEOUT_S,
    )
    app.state.token_bucket = app.state.redis.register_script(TOKEN_BUCKET_LUA)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="Aegis Gateway", lifespan=lifespan)


@app.exception_handler(RedisError)
async def redis_unavailable(request: Request, exc: RedisError) -> JSONResponse:
    # Fail closed: without Redis the limits can't be enforced, so refuse traffic
    # rather than let it through unchecked.
    return JSONResponse(status_code=503, content={"detail": "Rate limiter unavailable"})


@app.get("/health")
async def health_check():
    return {"status": "OK"}


@app.post("/chat")
async def chat(body: ChatRequest, request: Request):
    allowed, retry_after = await take_token(
        request.app.state.token_bucket,
        body.user_id,
        config.RATE_LIMIT_CAPACITY,
        config.RATE_LIMIT_REFILL_PER_S,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )

    return {"echo": body.message, "from": body.user_id}
