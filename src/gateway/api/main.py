import math
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException

from gateway import config
from gateway.api.schemas import ChatRequest
from gateway.core.rate_limiter import TOKEN_BUCKET_LUA, take_token
from gateway.keys import key_hash


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


# --- Errors: every failure has the same shape, {"error": {"code", "message"}} ---


class GatewayError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, headers: dict | None = None):
        super().__init__(status_code, message, headers)
        self.code = code


def error_response(status_code: int, code: str, message: str, headers=None, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, **extra}},
        headers=headers,
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    # Also catches FastAPI's own errors (404 unknown route, 405 wrong method).
    code = getattr(exc, "code", "http_error")
    return error_response(exc.status_code, code, str(exc.detail), exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
        for e in exc.errors()
    ]
    return error_response(422, "invalid_request", "Request body is invalid", details=details)


@app.exception_handler(RedisError)
async def redis_unavailable(request: Request, exc: RedisError) -> JSONResponse:
    # Fail closed: without Redis neither keys nor limits can be checked, so refuse
    # traffic rather than let it through unchecked.
    return error_response(503, "backend_unavailable", "Gateway backend unavailable, retry shortly")


# --- Dependencies: who is calling, and are they within their limit? ---

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def authenticate(request: Request, api_key: str | None = Security(api_key_header)) -> dict:
    if not api_key:
        raise GatewayError(401, "missing_api_key", "Send your API key in the X-API-Key header")
    record = await request.app.state.redis.hgetall(f"apikey:{key_hash(api_key)}")
    if not record:
        raise GatewayError(401, "invalid_api_key", "API key is not valid")
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
    return {"status": "OK"}


@app.post("/chat")
async def chat(body: ChatRequest, client: dict = Depends(rate_limit)):
    return {"echo": body.message, "client_id": client["client_id"]}
