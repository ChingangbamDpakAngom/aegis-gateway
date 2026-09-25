"""Call the model backend (Ollama's /api/chat) and report token usage."""

import httpx2
from prometheus_client import Counter

from gateway import config

TOKENS = Counter("aegis_llm_tokens_total", "Tokens processed by the model", ["model", "kind"])


async def generate(http: httpx2.AsyncClient, message: str) -> dict:
    # ponytail: one request, one full answer (stream=False). Streaming tokens back
    # (SSE) makes the first word appear sooner; add it when a UI needs it.
    response = await http.post("/api/chat", json={
        "model": config.MODEL,
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
    TOKENS.labels(config.MODEL, "prompt").inc(usage["prompt_tokens"])
    TOKENS.labels(config.MODEL, "completion").inc(usage["completion_tokens"])
    return {"reply": data["message"]["content"], "model": config.MODEL, "usage": usage}
