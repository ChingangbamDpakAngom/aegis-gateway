# ADR-003: One JSON log line per request, Prometheus metrics, liveness vs readiness

**Status:** Accepted · **Date:** 2026-09-25

## Context

The gateway makes decisions (401, 429, 503) that nobody could see after the fact. There was no way to answer "why was client X refused at 14:02?", "what's our p95 latency?" or "is this instance able to serve?".

## Decision

- **Request IDs.** Every response carries `X-Request-ID`. A caller's ID is reused only if it matches `[A-Za-z0-9._-]{1,64}`. Anything else is replaced with a fresh UUID, so callers can't inject fake log fields or huge values.
- **One JSON log line per request**, written by a single middleware: `ts, request_id, method, route, status, error_code, client_id, duration_ms`. Uses stdlib `logging` + `json`. No structlog: one flat event per request doesn't need it. **The API key and the message are never logged**, because logs are copied to more places, and read by more people, than the data itself.
- **Prometheus metrics** via `prometheus-client` at `/metrics`: `aegis_requests_total{route,status}` and `aegis_request_duration_seconds{route}` (histogram → p50/p95/p99 in PromQL). Labels use the **route template**, never the raw path, and never `client_id`. Every distinct label value creates a new time series. Scanners hitting random URLs would explode the series count ("cardinality"), and so would per-client labels. Per-client questions are answered from the logs.
- **Liveness ≠ readiness.** `/health` says the process is alive and never touches Redis. `/ready` pings Redis and returns 503 if it can't. An orchestrator restarts on failed liveness and stops routing traffic on failed readiness. If `/health` checked Redis, a Redis blip would restart every healthy gateway instance at once.

## Consequences

- `/metrics` is unauthenticated, which is normal for Prometheus targets. It must stay on an internal network or a separate port when deployed.
- Metrics live in process memory. With several workers or instances, Prometheus scrapes each one and sums them. Multi-process mode for gunicorn workers is needed only if we run that way.
- No distributed tracing yet. The request ID plays that role while there's a single service. Add OpenTelemetry once the gateway calls a model backend, so a trace spans gateway → model.
- No Grafana or Prometheus server in `docker-compose.yml` yet. `curl /metrics` shows the raw numbers. Add a dashboard when there's real traffic to look at (Phase 7).
