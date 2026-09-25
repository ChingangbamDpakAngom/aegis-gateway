# ADR-004: Ollama as the first model backend, with hard caps and explicit failure codes

**Status:** Accepted · **Date:** 2026-09-25

## Context

`/chat` only echoed the message. The gateway's reason to exist is to sit in front of a model, so it needs a real one. The one it calls must be free to run, work offline on a laptop, and be replaceable later without rewriting the gateway.

## Decision

- **Ollama, running locally, with `llama3.2` (3B) by default.** It's free, runs on CPU or a small GPU, and exposes a plain HTTP API (`POST /api/chat`). vLLM is faster at scale but needs a proper GPU server. Hosted APIs cost money per call and need secrets. Both can be added later as extra backends for routing.
- **The gateway calls it over HTTP with one pooled `httpx2.AsyncClient`**, opened in the lifespan like the Redis pool. It's async, so a slow generation doesn't block other requests. No SDK: one JSON POST doesn't need one.
- **Only requests that pass authentication, rate limiting and validation reach the model.** Inference is the expensive step, so every rejection happens before it. A test proves that a 429 never calls the model.
- **Hard caps:** `num_predict = MAX_OUTPUT_TOKENS` (512) bounds answer length, and so latency and cost. The timeout is 2 s to connect and `MODEL_TIMEOUT_S` (60 s) overall.
- **Explicit failure codes.** Ollama down or erroring (e.g. model not pulled) → `502 model_unavailable`. Too slow → `504 model_timeout`. Both use the standard error shape, and both differ from `503 backend_unavailable`, which means *Redis* is down. An operator can tell the three apart from the status code alone.
- **Token usage is first-class**: returned in `usage`, logged per request, and counted in `aegis_llm_tokens_total{model,kind}`. Tokens are the unit of LLM cost, and later phases (budgets, routing, caching) all need them.
- **No streaming yet** (`stream: false`). The whole answer is returned at once. Streaming (server-sent events) improves time-to-first-token for chat UIs. Add it when there is one.

## Consequences

- Ollama is a runtime dependency. `/ready` still checks only Redis. A model outage fails each `/chat` with 502, but doesn't pull the instance out of the load balancer, because every instance shares the same backend and removing them all would help nobody.
- **Cold starts:** the first request after the model has been idle (Ollama unloads it after about 5 minutes) includes loading it into memory. On a laptop this took over 60 s once and correctly produced a 504. Remedies: raise `MODEL_TIMEOUT_S`, or keep the model loaded (`OLLAMA_KEEP_ALIVE`).
- Rate limits still count requests, not tokens. A client can send 10 long prompts as cheaply as 10 short ones. Token budgets per client are a later phase.
- Tests use `httpx2.MockTransport` in place of Ollama, so CI stays fast and needs no model. A live smoke test runs when `AEGIS_LIVE_MODEL=1` is set.
