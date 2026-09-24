<div align="center">

# Aegis Gateway

### A protective checkpoint for AI applications

Control requests today. Build toward safer, more efficient AI systems tomorrow.

![Status: v0.1.0 MVP](https://img.shields.io/badge/status-v0.1.0%20MVP-243B53?style=flat-square)
![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![CI](https://github.com/ChingangbamDpakAngom/aegis-gateway/actions/workflows/ci.yml/badge.svg)

</div>

---

Aegis Gateway is being built to sit between users and AI models. Like a checkpoint at a building entrance, it checks incoming requests before they are allowed through. This helps keep an AI application reliable when usage grows and creates one place to add safeguards, caching, and smarter model selection.

> **What works now:** API-key authentication, strict request validation, and per-client Redis rate limiting that fails closed. The `/chat` endpoint currently echoes an allowed message; it does **not** call an AI model yet. Prompt screening, caching, routing, and observability are planned, not shipped.

## Why it matters

| Challenge | How a gateway can help |
|---|---|
| Anyone can call the service | Require an API key and decide identity server-side |
| One client sends too many requests | Limit requests per client before they overwhelm the service |
| Requests are malformed or oversized | Reject invalid inputs before processing |
| Repeated AI requests cost money | A future cache can reuse suitable responses |
| Requests vary in difficulty | A future router can select an appropriate model |
| Suspicious prompts reach a model | A future screening step can flag potential attacks |

The first three protections are implemented. The remaining items describe the roadmap.

## Current request flow

The diagram below represents the running application, not the eventual architecture.

```mermaid
flowchart TD
    A["POST /chat<br/>X-API-Key header"] --> B{"Known key?<br/>SHA-256 lookup in Redis"}
    B -->|"No"| X["401 missing/invalid_api_key"]
    B -->|"Yes"| C{"Token in client's bucket?<br/>Redis + Lua"}
    C -->|"No"| Y["429 rate_limited + Retry-After"]
    C -->|"Yes"| D{"Valid body?<br/>Pydantic"}
    D -->|"No"| Z["422 invalid_request"]
    D -->|"Yes"| E["200 OK<br/>Echo the message"]
```

- `/health` reports whether the API process is running.
- `/chat` requires an `X-API-Key` header and a body of exactly `{"message": "..."}` (1–4000 characters; unknown fields are rejected). Identity comes from the key, never from the body ([ADR-002](docs/adr/0002-api-key-identity.md)).
- Keys are stored only as SHA-256 hashes. Each client has its own bucket (default capacity 10, refill 1 token/s), and a key can carry its own limits. Keys of the same client share a bucket, so rotating a key doesn't reset the limit.
- Every error has the same shape: `{"error": {"code": "...", "message": "..."}}`.
- Redis runs the token-bucket check and update in one Lua script, so two simultaneous requests cannot spend the same token. The script uses Redis's clock, so every gateway instance agrees on the time.
- When the bucket is empty, the `429` response carries a `Retry-After` header saying how many seconds to wait.
- If Redis is unreachable, `/chat` **fails closed** with `503` within about half a second instead of serving requests unchecked ([ADR-001](docs/adr/0001-token-bucket-in-redis-fail-closed.md)).

## Tools and technologies

| Tool | Role | Status |
|---|---|---|
| Python, FastAPI, Pydantic | API endpoints and request validation | Implemented |
| Redis and Lua scripting | Shared token-bucket rate limiting | Implemented |
| Docker Compose | Runs Redis for local development | Implemented |
| uv | Python environment and dependency management | Implemented |
| pytest and FastAPI TestClient | Basic API tests | Implemented |
| Llama Prompt Guard 2 | Candidate prompt-injection screening model; access and integration still need to be resolved | Planned |
| Redis cache | Exact-match response caching | Planned |
| Local model runtime, such as vLLM, and/or model APIs | Generate answers after checks; provider choice is not final | Planned |
| Rule-based router | Select an appropriate model for a request | Planned |
| Structured logging | Track gateway decisions and errors | Planned |
| OpenTelemetry, Prometheus, Grafana | Deeper tracing, metrics, and dashboards | Future exploration |

## Planned gateway flow

This is the intended direction, not a claim about current functionality.

```mermaid
flowchart TD
    A["Incoming request"] --> B["Validate"]
    B --> C["Rate limit"]
    C --> D["Screen suspicious prompts"]
    D --> E["Check answer cache"]
    E -->|"Hit"| F["Return cached answer"]
    E -->|"Miss"| G["Choose model"]
    G --> H["Generate and return answer"]
```

## Run locally

### Prerequisites

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop with its engine running

From a terminal:

```bash
git clone https://github.com/ChingangbamDpakAngom/aegis-gateway.git
cd aegis-gateway
uv sync
docker compose up -d
uv run uvicorn gateway.api.main:app --reload
```

Open the interactive API documentation at:

```text
http://127.0.0.1:8000/docs
```

### Create an API key

```bash
uv run python -m gateway.keys my-app                 # default limits
uv run python -m gateway.keys my-app --capacity 5 --refill-per-s 0.5
```

The key (`aeg_...`) is printed **once**. Only its hash is stored.

### Try a request

In the `/docs` page click **Authorize** and paste the key, or run:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "X-API-Key: aeg_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello Aegis"}'
```

While a token is available, the response is:

```json
{"echo":"Hello Aegis","client_id":"my-app"}
```

When the client's bucket is empty, the API returns HTTP `429` with a `Retry-After` header:

```json
{"error":{"code":"rate_limited","message":"Rate limit exceeded"}}
```

The health endpoint is available at:

```text
http://127.0.0.1:8000/health
```

## Testing

Start Redis before running the test suite:

```bash
docker compose up -d
uv run pytest -q
```

Tests run against a real Redis, using database 15 (flushed before each test) so they never touch development data. The suite covers API-key authentication (missing, unknown, stored hashed), request validation (empty, oversized, unknown fields), the uniform error format, bucket exhaustion, `Retry-After`, token refill, separate per-client buckets, key rotation sharing a bucket, and fail-closed behaviour when Redis is down. GitHub Actions runs the same suite on every push, with a Redis service container.

Settings come from environment variables or a local `.env` file: `REDIS_URL`, `REDIS_TIMEOUT_S`, `RATE_LIMIT_CAPACITY`, `RATE_LIMIT_REFILL_PER_S` (defaults for keys without their own limits), `MAX_MESSAGE_CHARS` (see [`config.py`](src/gateway/config.py)).

The Prompt Guard experiment needs PyTorch, which is kept out of the default install: `uv sync --group ml`.

## Project layout

```text
aegis-gateway/
├── src/
│   └── gateway/
│       ├── api/
│       │   ├── __init__.py
│       │   ├── main.py            # FastAPI endpoints
│       │   └── schemas.py         # Request-validation models
│       ├── core/
│       │   ├── __init__.py
│       │   ├── rate_limiter.py    # Redis rate-limit integration
│       │   └── lua/
│       │       └── token_bucket.lua
│       ├── __init__.py
│       ├── config.py              # Settings from environment variables
│       ├── keys.py                # API-key creation CLI + hashing
│       └── py.typed
├── tests/                         # Automated tests (real Redis, DB 15)
├── docs/
│   ├── adr/                       # Architecture Decision Records
│   └── phases/                    # Per-phase design + study notes
├── .github/workflows/ci.yml       # Tests on every push
├── docker-compose.yml             # Local Redis service
├── pyproject.toml                 # Project configuration and dependencies
├── uv.lock                        # Locked dependency versions
├── .gitignore
└── README.md
```

Additional modules will be added as their features are implemented. Experimental scripts are kept separate from the running API and test suite.

## Roadmap

- [x] Validate incoming requests with FastAPI and Pydantic
- [x] Apply per-client rate limiting with Redis and Lua
- [x] Add basic API tests and verify the `429` path manually
- [x] Phase 0: async Redis, env config, fail-closed limiter, deterministic tests, CI ([notes](docs/phases/phase-0.md))
- [x] Phase 1: hashed API keys, per-client limits, strict request contract, uniform errors ([notes](docs/phases/phase-1.md))
- [ ] Phase 2: readiness checking, request IDs, structured logging, metrics
- [ ] Evaluate and integrate prompt-injection screening
- [ ] Add exact-match caching and model routing
- [ ] Connect an AI model and measure latency and cost
- [ ] Explore semantic caching and observability dashboards

## Versioning

`v0.1.0` marks the working validation and rate-limiting foundation. The roadmap is a plan, not a list of released features.
