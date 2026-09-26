# ADR-008: Ship as a Docker stack, load-test it, and coalesce identical in-flight requests

**Status:** Accepted · **Date:** 2026-09-25

## Context

v1.0 has to run for someone else with one command, show its health at a glance, and have numbers behind its performance claims. Until now it ran from a developer's shell, with Redis alone in Docker.

## Decision

- **One image, non-root, dependencies cached.** `python:3.12-slim` + uv. `uv.lock` is copied and synced *before* the source, so code changes rebuild in seconds. It runs as user `aegis`, not root. The default image is **372 MB**, with no guard.
- **The guard is a build option** (`WITH_GUARD=true`). The lock file's Linux PyTorch pulls ~3 GB of CUDA libraries, so the guard build installs the CPU-only wheel instead. The image is still **3.07 GB**, which is why it isn't the default. The downloaded classifier lives in a named volume (`hf-cache`), so it's fetched once, not on every start.
- **Compose profiles.** Plain `docker compose up -d` still starts only Redis, so the dev and test workflow and CI are unchanged. `--profile stack` adds the gateway, Prometheus and Grafana. Ollama stays on the host, where it can use the GPU. The gateway reaches it at `host.docker.internal`.
- **Health is `/ready`**, so Docker marks the gateway healthy only once Redis answers *and* startup has finished (including loading the guard).
- **Observability as code.** `ops/prometheus.yml` scrapes the gateway every 5 s. Grafana's datasource and a 6-panel dashboard are provisioned from files, with no clicking. Anonymous read-only access is for the local demo only.
- **Load test** (`scripts/load_test.py`, async httpx2 with a concurrency cap) in three scenarios: `cached` (gateway overhead), `ratelimit` (429 behaviour) and `model` (LLM-bound).
- **CI also builds the Docker image**, so a broken Dockerfile fails the pipeline.

## What the load test found

| Run | Result |
|---|---|
| cached, 2000 req, concurrency 20 | 246 req/s, p50 73 ms, p95 109 ms, all 200 |
| ratelimit, 200 req at a 10-token key, **before** the fix | 11 × 200, 189 × 429, **p95 12.4 s**: the 11 allowed requests all missed the cache together and made **11** model calls (6–12.6 s each, queued on Ollama) |
| same, **after** the fix | **1** model call, 10 `coalesced`; p95 3.1 s, 61 req/s |
| model, 12 new questions, concurrency 4 | 0.9 req/s, p50 3.0 s, p95 6.7 s, bounded by the laptop GPU |

**Fix: request coalescing ("single-flight").** `cache.single_flight()` keeps the in-flight model call per `client + message digest`. Identical requests that arrive while it's running await the same task, marked `"cache": "coalesced"` with 0 tokens. `asyncio.shield` stops one impatient client from cancelling everyone's call. It's per process. With several instances, a Redis `SET NX` lock would be needed (`Trade-off:` note).

Two monitoring bugs also only showed up with real traffic:

- **Histogram buckets:** the default Prometheus buckets stop at 10 s, so the dashboard's p95 was flat at "10 s" while real LLM calls took minutes. Buckets now go up to 300 s.
- **Counters that start mid-burst:** three `400`s inside one scrape created the series already at 3, and `rate()`/`increase()` saw no increase, so "injection blocked" read 0. The expected `/chat` status series are now created at 0 on startup.

A first attempt at the `model` scenario reported 16-minute latencies and client timeouts, while the gateway's own logs showed every request finishing in under 36 s. It didn't reproduce. The client-to-container path (Docker Desktop on Windows) is the suspect, which is why the table uses the clean rerun, and why the gateway's own `duration_ms` is the number to trust.

## Consequences

- Throughput numbers are one laptop, one uvicorn worker. They show relative behaviour (cache vs model, coalescing) rather than production capacity. More workers or instances scale the gateway part. The model part needs more GPU.
- The guard image is large. A separate classifier service, or ONNX/quantised weights, would shrink it.
- Grafana's anonymous access and the unauthenticated `/metrics` are for local use. Put both behind auth or a private network before any deployment.
