<div align="center">

# Aegis Gateway

### A protective checkpoint for AI applications

Control requests today. Build toward safer, more efficient AI systems tomorrow.

![Status: v0.1.0 MVP](https://img.shields.io/badge/status-v0.1.0%20MVP-243B53?style=flat-square)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Tests: 3 passing](https://img.shields.io/badge/tests-3%20passing-2E7D32?style=flat-square)

</div>

---

Aegis Gateway is being built to sit between users and AI models. Like a checkpoint at a building entrance, it checks incoming requests before they are allowed through. This helps keep an AI application reliable when usage grows and creates one place to add safeguards, caching, and smarter model selection.

> **What works in v0.1.0:** Request validation and Redis-backed rate limiting. The `/chat` endpoint currently echoes an allowed message; it does **not** call an AI model. Security screening, caching, routing, and observability are planned features, not shipped features.

## Why it matters

| Challenge | How a gateway can help |
|---|---|
| One user sends too many requests | Limit requests per user before they overwhelm the service |
| Requests are missing required data | Reject invalid inputs before processing |
| Repeated AI requests cost money | A future cache can reuse suitable responses |
| Requests vary in difficulty | A future router can select an appropriate model |
| Suspicious prompts reach a model | A future screening step can flag potential attacks |

The first two protections are implemented. The remaining items describe the roadmap.

## Current request flow

The diagram below represents the running v0.1.0 application, not the eventual architecture.

```mermaid
flowchart TD
    A["POST /chat"] --> B["Validate request<br/>FastAPI + Pydantic"]
    B --> C["Check user limit<br/>Redis + Lua token bucket"]
    C -->|"Allowed"| D["200 OK<br/>Echo the message"]
    C -->|"Limit reached"| E["429 Too Many Requests"]
```

- `/health` reports whether the API process is running.
- `/chat` expects a `user_id` and `message`. Missing fields receive a validation error.
- Each user has a separate bucket with a capacity of 10 requests and a refill rate of 1 token per second.
- Redis runs the token-bucket check and update in one Lua script, so two simultaneous requests cannot spend the same token.

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

- Python 3.11 or later
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

### Try a request

Use the interactive `/docs` page, or run:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-123","message":"Hello Aegis"}'
```

While a token is available, the response is:

```json
{"echo":"Hello Aegis","from":"user-123"}
```

When the user's bucket is empty, the API returns HTTP `429`:

```json
{"detail":"Rate limit exceeded"}
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

The v0.1.0 automated suite currently verifies:

- `GET /health` returns `200 OK`.
- A valid `POST /chat` request returns `200 OK` with the expected echo response.
- An invalid chat request is rejected by request validation.

Rate limiting has also been verified manually with repeated requests from the same `user_id`: requests are allowed while tokens remain and receive HTTP `429 Too Many Requests` when the bucket is exhausted.

> **Test coverage note:** Deterministic automated tests for bucket exhaustion, token refill, separate user buckets, and Redis-unavailable behaviour are the next testing milestone.

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
│       └── py.typed
├── tests/                         # Automated tests
├── docs/
│   └── adr/                       # Architecture Decision Records
├── docker-compose.yml             # Local Redis service
├── pyproject.toml                 # Project configuration and dependencies
├── uv.lock                        # Locked dependency versions
├── .gitignore
└── README.md
```

Additional modules will be added as their features are implemented. Experimental scripts are kept separate from the running API and test suite.

> **Note:** Git does not track empty folders. Add a Markdown file to `docs/adr/` before relying on that directory being present in the GitHub repository.

## Roadmap

- [x] Validate incoming requests with FastAPI and Pydantic
- [x] Apply per-user rate limiting with Redis and Lua
- [x] Add basic API tests and verify the `429` path manually
- [ ] Add deterministic rate-limit and Redis-failure tests
- [ ] Add readiness checking and structured logging
- [ ] Evaluate and integrate prompt-injection screening
- [ ] Add exact-match caching and model routing
- [ ] Connect an AI model and measure latency and cost
- [ ] Explore semantic caching and observability dashboards

## Versioning

`v0.1.0` marks the working validation and rate-limiting foundation. The roadmap is a plan, not a list of released features.
