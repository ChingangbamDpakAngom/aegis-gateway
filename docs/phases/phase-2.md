# Phase 2 — Observability

**Goal:** every decision the gateway makes can be seen afterwards. Which request, which client, what happened, how long it took, and whether this instance can serve right now.

## What changed and why

| Before | After | Where |
|---|---|---|
| `/health` was the only signal | `/health` = alive (no Redis), `/ready` = can serve (pings Redis → 200 or 503) | `api/main.py` |
| Requests were anonymous in logs | Every response has `X-Request-ID`. A safe caller ID is reused, otherwise a UUID is generated | `observability.py` → `observe()` |
| Uvicorn's access log only (`"POST /chat" 429`) | One JSON line per request: `request_id, route, status, error_code, client_id, duration_ms` | `observability.py` |
| No numbers | Prometheus `/metrics`: request counter by route and status, latency histogram by route | `observability.py`, `main.py` → `metrics()` |
| — | Error handlers and `authenticate()` leave `error_code` / `client_id` on `request.state` for the log line | `main.py` → `error_response()`, `authenticate()` |

## The request, step by step

```
request ─► observe() middleware ─────────────────────────────────────────┐
             pick request_id, start timer                                │
             └─► FastAPI: authenticate → rate_limit → chat               │
                   (authenticate sets state.client_id;                   │
                    error handlers set state.error_code)                 │
           ◄── response                                                  │
             add X-Request-ID header                                     │
             finally: count metric, observe latency, write 1 JSON line ◄─┘
```

`finally` runs even when the app crashes, so a 500 still gets counted and logged.

Example log line for a rate-limited call:

```json
{"ts": "2026-09-25T10:02:11.402+00:00", "request_id": "3f1c…", "method": "POST", "route": "/chat", "status": 429, "error_code": "rate_limited", "client_id": "alice", "duration_ms": 2.31}
```

## Try it yourself

```bash
docker compose up -d
uv run uvicorn gateway.api.main:app
curl -i localhost:8000/health                                   # see X-Request-ID
curl -i -H "X-Request-ID: my-trace-1" localhost:8000/ready      # your ID comes back
curl -s localhost:8000/metrics | grep aegis_                    # counters + histogram buckets
docker compose stop redis && curl -i localhost:8000/ready       # 503, yet /health is still 200
docker compose start redis
```

Watch the uvicorn terminal: each request prints one JSON line.

## Prerequisites to study

1. **The three pillars: logs, metrics, traces.** What question each one answers. [Google SRE book — Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/) (the four golden signals)
2. **Structured logging.** Why JSON beats free text when a machine searches logs. [Python Logging HOWTO](https://docs.python.org/3/howto/logging.html)
3. **Prometheus data model.** Counter vs gauge vs histogram, labels, and why cardinality matters. [Metric types](https://prometheus.io/docs/concepts/metric_types/), [Naming & labels](https://prometheus.io/docs/practices/naming/), [Histograms](https://prometheus.io/docs/practices/histograms/)
4. **PromQL basics.** `rate()` and `histogram_quantile()`. [Querying basics](https://prometheus.io/docs/prometheus/latest/querying/basics/)
5. **Liveness vs readiness probes.** [Kubernetes probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
6. **ASGI middleware in FastAPI.** [FastAPI middleware](https://fastapi.tiangolo.com/tutorial/middleware/)
7. **Log injection / logging sensitive data.** [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
8. **Request / correlation IDs.** Why one ID should follow a request across services. [W3C Trace Context](https://www.w3.org/TR/trace-context/) (overview only).

## Review checklist — can you explain…

- [ ] Why `/health` must *not* check Redis, and what would go wrong in Kubernetes if it did?
- [ ] Why a caller's `X-Request-ID` is validated before being reused (what is log injection)?
- [ ] Why the metric label is the route template (`/chat`), not `request.url.path`, and why `client_id` isn't a label either?
- [ ] Counter vs histogram: why latency is a histogram, and how you'd get p95 from it?
- [ ] Why the logging happens in `finally`, and what `status = 500` is for?
- [ ] How `client_id` set in `authenticate()` reaches the middleware (`request.state`)?
- [ ] Why the API key and message body are never logged?
- [ ] What the `ponytail:` comment on `/metrics` warns about?

## Interview angle

"How would you know your service is healthy, and debug a customer complaint?" Golden signals from `/metrics` (traffic, errors, latency). Search the logs for the customer's `request_id` to see exactly which rule refused them. Separate liveness and readiness so a dependency outage doesn't cause restart storms. Tests prove each piece.
