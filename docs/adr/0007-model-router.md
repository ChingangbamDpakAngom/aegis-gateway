# ADR-007: Rule-based routing between a small and a large model, each the other's fallback

**Status:** Accepted · **Date:** 2026-09-25

## Context

One model for everything forces a bad trade-off. A small model is fast and cheap but weaker at multi-step reasoning. A large one is better but much slower and costlier. On this laptop (4 GB GPU) `llama3.2` (3B) generates ~40 tokens/s. `gemma2:9b` manages ~4.5 tokens/s because most of it runs on the CPU. A single model is also a single point of failure: if it isn't pulled, or hangs, every request fails.

## Decision

- **Two tiers.** `MODEL` (small, `llama3.2`) is the default. `LARGE_MODEL` (`gemma2:9b`) is for messages that look hard. Setting `LARGE_MODEL=""` turns routing off.
- **Explainable rules, in order:**
  1. length ≥ `ROUTE_LONG_CHARS` (1000) → large (`long`)
  2. contains "step by step", "explain why", "prove", "analyse/analyze", "compare", "debug" or a code block → large (`hard_hint`)
  3. otherwise → small (`default`)

  Rules are cheap (microseconds), deterministic and easy to debug from a log line. A learned router (a classifier trained on which model answered well, as in RouteLLM) needs labelled quality data we don't have yet.
- **Fallback in both directions.** The chosen model is tried first, then the other, on any backend error (connection refused, model not found, 5xx, timeout). If both fail, the last error surfaces as before (502/504). A slow answer from the "wrong" model beats an error.
- **Per-model timeouts:** 60 s for the small model, `LARGE_MODEL_TIMEOUT_S` 180 s for the large. With one shared 60 s timeout the large model would almost always time out and fall back, so routing would do nothing.
- **Visible decisions:** `route`, `model` and `fallback` in the response. `route_reason`, `model` and `fallback` in the log line (not `route`, which already means the URL route). `aegis_route_total{reason,model,outcome}` for traffic split and failure rate per model.
- **Cache keys include both model names**, so changing either model never serves answers from the old one.

## Measured (laptop, 150-token cap)

| Request | Route | Model | Time |
|---|---|---|---|
| Simple question (model warm) | default | llama3.2 | 1.8 s |
| "Explain step by step…" | hard_hint | gemma2:9b | 59 s |
| Simple question right after the large one | default | llama3.2 | 15.6 s (reload) |
| Hard question, large model missing | hard_hint → fallback | llama3.2 | 2.7 s |

## Consequences

- **Keyword rules misfire.** "Compare prices of two phones" goes to the large model and "what's 17 × 23?" doesn't. They're a starting point to tune against logs (`route_reason` + latency + user feedback), not a quality guarantee.
- **Model swapping hurts on small hardware.** Both models don't fit in memory together, so switching evicts the other and costs 15–30 s of reload. In production each tier would be its own always-loaded server, or a hosted API.
- **Fallback can double the wait.** A large-model timeout (up to 180 s) followed by a small-model answer is slow. That's acceptable for a demo. In production, cap total time per request.
- Hard questions cost ~30× the latency of easy ones. Rate limits still count requests, not cost. Token- or cost-weighted limits are the natural next step.
- Adding a hosted API tier later means one more `(model, timeout)` entry and a backend client. The router's shape doesn't change.
