# Phase 6 — Model routing with fallback

**Goal:** send each message to the cheapest model that can handle it, and keep answering when a model fails.

## What changed and why

| Before | After | Where |
|---|---|---|
| One model (`MODEL`) for everything | Rules pick `llama3.2` (default) or `gemma2:9b` (long / reasoning / code) | `core/router.py` → `choose()` |
| A model failure was a 502/504 | The other model is tried first; 502/504 only if both fail | `router.py` → `complete()` |
| One 60 s timeout | Per-model timeouts (large model: 180 s) | `llm.py` → `generate(..., model, timeout_s)`, `config.py` |
| Response said which model | Response adds `route` and `fallback`; log adds `route_reason`, `model`, `fallback` | `main.py`, `observability.py` |
| — | `aegis_route_total{reason,model,outcome}` | `router.py` |
| Cache key used one model name | Cache key uses both model names | `cache.py` → `_digest()` |

## The request, step by step

```
POST /chat → auth → rate limit → validate → guard → cache miss
  └─ router.complete(message)
        choose():  len ≥ 1000            → "long"      → [gemma2:9b (180 s), llama3.2 (60 s)]
                   "step by step", "compare",
                   "debug", ``` …        → "hard_hint" → [gemma2:9b, llama3.2]
                   otherwise             → "default"   → [llama3.2, gemma2:9b]
        try each in order; on any httpx2.HTTPError try the next
        all failed → re-raise the last error → 502 / 504
  → 200 {reply, model, usage, route, fallback, cache, client_id}
```

## Measured

| Request | Route | Model | Time |
|---|---|---|---|
| Simple question (warm) | default | llama3.2 | 1.8 s |
| "Explain step by step…" | hard_hint | gemma2:9b | 59 s |
| Simple question after the large one | default | llama3.2 | 15.6 s (model reload) |
| Hard question, large model missing | hard_hint + fallback | llama3.2 | 2.7 s |

## Try it yourself

```bash
ollama pull gemma2:9b                      # ~5.4 GB; optional, hard questions fall back without it
uv run uvicorn gateway.api.main:app
curl -s -X POST localhost:8000/chat -H "X-API-Key: <key>" -H "Content-Type: application/json" -d '{"message":"What is Redis?"}'
curl -s -X POST localhost:8000/chat -H "X-API-Key: <key>" -H "Content-Type: application/json" -d '{"message":"Explain step by step why 2 odd numbers sum to an even number"}'
ollama ps                                  # watch which model is loaded, and where (CPU/GPU)
LARGE_MODEL=no-such-model uv run uvicorn gateway.api.main:app   # then send a hard question: fallback=true
curl -s localhost:8000/metrics | grep aegis_route_total
```

## Must-know core (job-ready)

1. **Why route at all:** cost and latency scale with model size. Most traffic is easy, so send it to the small model and pay for the big one only when needed.
2. **Rule-based vs learned routing:** rules are explainable and free but crude. Learned routers (e.g. RouteLLM) predict "will the small model do well enough?" from data. Start with rules, log decisions, and learn later.
3. **Fallback / failover:** retry on a *different* backend when one fails. It's the same idea as load-balancer failover.
4. **Timeouts per dependency:** each backend gets a timeout matching its expected latency. One global timeout either kills slow-but-healthy calls or waits too long on dead ones.
5. **Which errors to fall back on:** backend failures (connect, 5xx, 404 model missing, timeout), yes. The client's own bad input, no (it's rejected earlier with 4xx).
6. **Observability of routing:** without `route_reason` / `model` / `fallback` in logs and metrics, you can't tune rules or notice that one model is failing all the time.

**Likely interview questions:** "How would you reduce LLM costs?" (caching, routing, output caps: point to Phases 3, 5, 6). "What happens when your LLM provider is down?" (fallback plus distinct 502/504). "How would you decide which requests need the big model?" (rules now, a learned router trained on logged outcomes later).

**Skip for now:** RouteLLM internals, bandit/RL routing, retries with exponential backoff and jitter, circuit breakers. (Know the names: a circuit breaker stops calling a backend that keeps failing for a cool-down period.)

## Study links

- [RouteLLM blog (LMSYS)](https://lmsys.org/blog/2024-07-01-routellm/): the case for learned routing, first half only
- [Ollama FAQ — keep_alive and memory](https://github.com/ollama/ollama/blob/main/docs/faq.md): why models get unloaded
- [Martin Fowler — Circuit Breaker](https://martinfowler.com/bliki/CircuitBreaker.html): the pattern you'd add next
- [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/): per-request timeouts

## Review checklist — can you explain…

- [ ] The three routing rules, their order, and one message each rule gets wrong?
- [ ] Why the large model needs its own timeout, and what would happen with one shared 60 s timeout?
- [ ] What `complete()` does when the first model raises, and when every model raises?
- [ ] Why `ReadTimeout` and a 404 "model not found" both trigger fallback?
- [ ] Why the log field is `route_reason` and not `route` (what bug did the tests catch)?
- [ ] Why the cache key now includes both model names?
- [ ] Why the simple question took 15.6 s right after the large one?
- [ ] How you'd find out from `/metrics` that `gemma2:9b` is failing a lot?

## Interview angle

"Design an LLM gateway that keeps costs down and stays up." Walk the pipeline: auth → rate limit → guard → cache → router → model with fallback. Show the table: an easy question on the small model takes 1.8 s, a hard one on the large model 59 s, and fallback keeps answering when a model is missing. Then name the limits honestly: keyword rules misfire, swapping models on one small GPU costs a reload, and the next step is a learned router trained on logged outcomes.
