"""Call the model backend (any OpenAI-compatible chat API) and report token usage."""

import httpx2
from prometheus_client import Counter

from gateway import config

TOKENS = Counter("aegis_llm_tokens_total", "Tokens processed by the model", ["model", "kind"])


async def generate(http: httpx2.AsyncClient, message: str, model: str, timeout_s: float) -> dict:
    # Trade-off: one request, one full answer (stream=False). Streaming tokens back
    # (SSE) makes the first word appear sooner; add it when a UI needs it.
    # OpenAI-compatible chat API: the same request works for local Ollama and hosted APIs.
    response = await http.post("/chat/completions", timeout=httpx2.Timeout(timeout_s, connect=2.0), json={
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "stream": False,
        "max_tokens": config.MAX_OUTPUT_TOKENS,
    })
    response.raise_for_status()  # e.g. 404 when the model isn't pulled -> handled as 502
    data = response.json()

    reported = data.get("usage") or {}  # some servers omit usage; count it as 0, not an error
    usage = {
        "prompt_tokens": reported.get("prompt_tokens", 0),
        "completion_tokens": reported.get("completion_tokens", 0),
    }
    TOKENS.labels(model, "prompt").inc(usage["prompt_tokens"])
    TOKENS.labels(model, "completion").inc(usage["completion_tokens"])
    return {"reply": data["choices"][0]["message"]["content"], "model": model, "usage": usage}
