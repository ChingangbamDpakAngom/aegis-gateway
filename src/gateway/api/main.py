import asyncio
import math
from contextlib import asynccontextmanager

import httpx2

from fastapi import Depends, FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException

from gateway import config
from gateway.api.schemas import ChatRequest
from gateway.core import cache, guard, router
from gateway.core.rate_limiter import TOKEN_BUCKET_LUA, take_token
from gateway.keys import key_hash
from gateway.observability import observe


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
    # One pooled HTTP client for the model backend. Connecting fails fast (Ollama down
    # -> 502 in ~2 s); generating may take up to MODEL_TIMEOUT_S.
    app.state.http = httpx2.AsyncClient(
        base_url=config.OLLAMA_URL, timeout=httpx2.Timeout(config.MODEL_TIMEOUT_S, connect=2.0)
    )
    # The classifier takes a few seconds to load, so load it once, here, not per request.
    app.state.guard = guard.load(config.GUARD_MODEL) if config.GUARD_ENABLED else None
    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()


app = FastAPI(title="Aegis Gateway", lifespan=lifespan)
app.middleware("http")(observe)


# --- Errors: every failure has the same shape, {"error": {"code", "message"}} ---


class GatewayError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, headers: dict | None = None):
        super().__init__(status_code, message, headers)
        self.code = code


def error_response(
    request: Request, status_code: int, code: str, message: str, headers=None, **extra
) -> JSONResponse:
    request.state.error_code = code  # picked up by the request log line
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, **extra}},
        headers=headers,
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    # Also catches FastAPI's own errors (404 unknown route, 405 wrong method).
    code = getattr(exc, "code", "http_error")
    return error_response(request, exc.status_code, code, str(exc.detail), exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
        for e in exc.errors()
    ]
    return error_response(request, 422, "invalid_request", "Request body is invalid", details=details)


@app.exception_handler(RedisError)
async def redis_unavailable(request: Request, exc: RedisError) -> JSONResponse:
    # Fail closed: without Redis neither keys nor limits can be checked, so refuse
    # traffic rather than let it through unchecked.
    return error_response(request, 503, "backend_unavailable", "Gateway backend unavailable, retry shortly")


@app.exception_handler(httpx2.TimeoutException)
async def model_timeout(request: Request, exc: httpx2.TimeoutException) -> JSONResponse:
    return error_response(request, 504, "model_timeout", "The model took too long to answer")


@app.exception_handler(httpx2.HTTPError)
async def model_unavailable(request: Request, exc: httpx2.HTTPError) -> JSONResponse:
    # Connection refused, or Ollama answered with an error (e.g. model not pulled).
    # 502: the gateway is fine, the server behind it isn't.
    return error_response(request, 502, "model_unavailable", "The model backend is unavailable")


# --- Dependencies: who is calling, and are they within their limit? ---

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def authenticate(request: Request, api_key: str | None = Security(api_key_header)) -> dict:
    if not api_key:
        raise GatewayError(401, "missing_api_key", "Send your API key in the X-API-Key header")
    record = await request.app.state.redis.hgetall(f"apikey:{key_hash(api_key)}")
    if not record:
        raise GatewayError(401, "invalid_api_key", "API key is not valid")
    request.state.client_id = record["client_id"]
    return record


async def rate_limit(request: Request, client: dict = Depends(authenticate)) -> dict:
    allowed, retry_after = await take_token(
        request.app.state.token_bucket,
        client["client_id"],
        int(client.get("capacity", config.RATE_LIMIT_CAPACITY)),
        float(client.get("refill_per_s", config.RATE_LIMIT_REFILL_PER_S)),
    )
    if not allowed:
        raise GatewayError(
            429,
            "rate_limited",
            "Rate limit exceeded",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )
    return client


# --- Routes ---


@app.get("/health")
async def health_check():
    # Liveness: the process is up. Deliberately doesn't touch Redis, so a Redis
    # outage doesn't get healthy gateway processes restarted.
    return {"status": "OK"}


@app.get("/ready")
async def readiness_check(request: Request):
    # Readiness: can this instance serve /chat right now? A Redis failure becomes
    # 503 backend_unavailable via the handler above, so load balancers skip us.
    await request.app.state.redis.ping()
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    # ponytail: unauthenticated, like most Prometheus targets; keep it on an
    # internal network, or put it behind its own port/auth before exposing the gateway.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/chat")
async def chat(request: Request, body: ChatRequest, client: dict = Depends(rate_limit)):
    if request.app.state.guard is not None:
        # The classifier is CPU-bound; a worker thread keeps the event loop serving others.
        # ponytail: one request at a time per thread; batch requests if throughput matters.
        score = await asyncio.to_thread(request.app.state.guard, body.message)
        request.state.guard_score = round(score, 4)
        if score >= config.GUARD_THRESHOLD:
            raise GatewayError(400, "prompt_rejected", "The message looks like a prompt-injection attempt")
    redis, http, client_id = request.app.state.redis, request.app.state.http, client["client_id"]
    vector = None
    if config.CACHE_ENABLED:
        # After the guard, so a cached answer is never served for a message we'd now refuse.
        hit, vector = await cache.lookup(redis, http, client_id, body.message)
        if hit:
            request.state.cache, request.state.cache_similarity = hit["cache"], hit["similarity"]
            no_tokens = {"prompt_tokens": 0, "completion_tokens": 0}
            return {"reply": hit["reply"], "model": hit["model"], "usage": no_tokens,
                    "cache": hit["cache"], "client_id": client_id}

    if config.CACHE_ENABLED:
        # Identical questions arriving together share one model call (see cache.single_flight).
        result, leader = await cache.single_flight(
            f"{client_id}:{cache.digest(body.message)}", lambda: router.complete(http, body.message)
        )
    else:
        result, leader = await router.complete(http, body.message), True
    request.state.model, request.state.route_reason = result["model"], result["route"]
    request.state.fallback = result["fallback"]
    if not leader:
        request.state.cache = "coalesced"
        no_tokens = {"prompt_tokens": 0, "completion_tokens": 0}
        return {**result, "usage": no_tokens, "cache": "coalesced", "client_id": client_id}

    request.state.usage = result["usage"]
    if config.CACHE_ENABLED:
        await cache.store(redis, client_id, body.message, result, vector)
        request.state.cache = "miss"
    return {**result, "cache": "miss", "client_id": client_id}
