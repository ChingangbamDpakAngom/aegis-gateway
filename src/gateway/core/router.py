"""Pick a model for each message by simple rules, and fall back if it fails."""

import httpx2
from prometheus_client import Counter

from gateway import config
from gateway.core.llm import generate

ROUTES = Counter("aegis_route_total", "Model calls by route reason, model and outcome",
                 ["reason", "model", "outcome"])

# Phrases that usually mean multi-step reasoning or code: worth the slower, larger model.
HARD_HINTS = ("step by step", "explain why", "prove", "analyse", "analyze", "compare", "debug", "```")


def choose(message: str) -> tuple[str, list[tuple[str, float]]]:
    """Return (reason, [(model, timeout_s), ...]) in the order to try them."""
    small = (config.MODEL, config.MODEL_TIMEOUT_S)
    if not config.LARGE_MODEL:
        return "single_model", [small]
    large = (config.LARGE_MODEL, config.LARGE_MODEL_TIMEOUT_S)

    text = message.lower()
    if len(message) >= config.ROUTE_LONG_CHARS:
        reason = "long"
    elif any(hint in text for hint in HARD_HINTS):
        reason = "hard_hint"
    else:
        return "default", [small, large]
    return reason, [large, small]


async def complete(http: httpx2.AsyncClient, message: str) -> dict:
    """Try the chosen model; on any backend failure try the next one.
    Raises the last error if every model fails (-> 502/504 as before)."""
    reason, candidates = choose(message)
    for attempt, (model, timeout_s) in enumerate(candidates):
        try:
            result = await generate(http, message, model, timeout_s)
        except httpx2.HTTPError:
            ROUTES.labels(reason, model, "error").inc()
            if attempt == len(candidates) - 1:
                raise
            continue
        ROUTES.labels(reason, model, "ok").inc()
        return {**result, "route": reason, "fallback": attempt > 0}
