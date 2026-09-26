"""Call the model backend (Ollama's /api/chat) and report token usage."""

import httpx2
from prometheus_client import Counter

from gateway import config

TOKENS = Counter("aegis_llm_tokens_total", "Tokens processed by the model", ["model", "kind"])


async def generate(http: httpx2.AsyncClient, message: str, model: str, timeout_s: float) -> dict:
    # Trade-off: one request, one full answer (stream=False). Streaming tokens back
    # (SSE) makes the first word appear sooner; add it when a UI needs it.
    response = await http.post("/api/chat", timeout=httpx2.Timeout(timeout_s, connect=2.0), json={
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "stream": False,
        "options": {"num_predict": config.MAX_OUTPUT_TOKENS},
    })
    response.raise_for_status()  # e.g. 404 when the model isn't pulled -> handled as 502
    data = response.json()

    # Ollama leaves prompt_eval_count out when the prompt was already cached.
    usage = {
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
    }
    TOKENS.labels(model, "prompt").inc(usage["prompt_tokens"])
    TOKENS.labels(model, "completion").inc(usage["completion_tokens"])
    return {"reply": data["message"]["content"], "model": model, "usage": usage}
