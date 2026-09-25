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
LATENCY = Histogram("aegis_request_duration_seconds", "Time to handle a request", ["route"])

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
            "duration_ms": round(duration * 1000, 2),
        }))
