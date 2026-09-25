"""Request IDs, one JSON log line per request, and Prometheus metrics."""

import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone

from fastapi import Request
from prometheus_client import Counter, Histogram

log = logging.getLogger("aegis")
log.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_handler)

REQUESTS = Counter("aegis_requests_total", "Requests handled", ["route", "status"])
# Start the expected series at 0. A series that first appears already at 3 (three 400s
# inside one scrape) shows no increase to rate()/increase(), so the dashboard reads 0.
for _status in ("200", "400", "401", "422", "429", "502", "503", "504"):
    REQUESTS.labels("/chat", _status)
# The default buckets stop at 10 s; LLM calls take minutes on a laptop, so p95 would be capped.
LATENCY = Histogram("aegis_request_duration_seconds", "Time to handle a request", ["route"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300))

# A caller-supplied ID is only reused if it is short and plain; anything else could
# forge log lines or bloat the logs, so a fresh ID replaces it.
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


async def observe(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    request_id = incoming if _SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex
    request.state.request_id = request_id
    start = time.perf_counter()

    status = 500  # stays 500 if the app raises something no handler catches
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration = time.perf_counter() - start
        # The route template (/chat), not the raw path: unknown paths from scanners
        # would otherwise create a new metric series each.
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUESTS.labels(route, str(status)).inc()
        LATENCY.labels(route).observe(duration)
        state = request.state
        # Never log the API key or the message: logs are read by more people than the data.
        log.info(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "method": request.method,
            "route": route,
            "status": status,
            "error_code": getattr(state, "error_code", None),
            "client_id": getattr(state, "client_id", None),
            "guard_score": getattr(state, "guard_score", None),
            "route_reason": getattr(state, "route_reason", None),
            "model": getattr(state, "model", None),
            "fallback": getattr(state, "fallback", None),
            "usage": getattr(state, "usage", None),
            "cache": getattr(state, "cache", None),
            "cache_similarity": getattr(state, "cache_similarity", None),
            "duration_ms": round(duration * 1000, 2),
        }))
