# Phase 3 — A real model behind `/chat`

**Goal:** allowed requests get a real answer from a local LLM. Failures of the model are reported clearly, and every answer's token cost is measured.

## What changed and why

| Before | After | Where |
|---|---|---|
| `/chat` echoed the message | `/chat` sends it to Ollama (`llama3.2`) and returns `{reply, model, usage, client_id}` | `core/llm.py` → `generate()`, `api/main.py` → `chat()` |
| — | One pooled async HTTP client to Ollama, opened and closed in the lifespan | `main.py` → `lifespan()` |
| — | Answer length capped (`num_predict = MAX_OUTPUT_TOKENS`), 2 s connect / 60 s total timeout | `llm.py`, `config.py` |
| — | Ollama down/erroring → `502 model_unavailable`, too slow → `504 model_timeout` | `main.py` → exception handlers |
| Logs/metrics had no cost signal | Token usage in the response, the log line, and `aegis_llm_tokens_total{model,kind}` | `llm.py`, `observability.py` |
| `httpx2` was a test-only dependency | It's now a runtime dependency (the gateway is an HTTP client too) | `pyproject.toml` |
| Tests needed nothing but Redis | Tests use a fake Ollama (`MockTransport`); one opt-in live test uses the real one | `tests/conftest.py`, `tests/test_api.py` |

## The request, step by step

```
POST /chat
  ├─ observe()          request ID, timer                         (Phase 2)
  ├─ authenticate()     401                                        (Phase 1)
  ├─ rate_limit()       429  ← nothing below runs, so no inference cost
  ├─ ChatRequest        422
  ├─ generate()         POST {OLLAMA_URL}/api/chat
  │     {"model": "llama3.2", "messages": [{"role": "user", "content": ...}],
  │      "stream": false, "options": {"num_predict": 512}}
  │     connection refused / 4xx-5xx from Ollama → 502 model_unavailable
  │     no answer within MODEL_TIMEOUT_S         → 504 model_timeout
  └─ 200 {"reply", "model", "usage": {"prompt_tokens", "completion_tokens"}, "client_id"}
```

Ollama's reply holds `message.content` (the answer), `prompt_eval_count` (input tokens) and `eval_count` (output tokens). `generate()` renames them to the OpenAI-style `prompt_tokens` / `completion_tokens` that most tools expect.

## Try it yourself

```bash
ollama pull llama3.2                       # once, ~2 GB
docker compose up -d
uv run python -m gateway.keys demo         # copy the aeg_... key
uv run uvicorn gateway.api.main:app
curl -X POST localhost:8000/chat -H "X-API-Key: <key>" -H "Content-Type: application/json" -d '{"message":"Explain a token bucket in one sentence"}'
curl -s localhost:8000/metrics | grep aegis_llm_tokens_total
ollama stop llama3.2                       # unload it, then time the next request: cold start
```

Stop Ollama (quit it from the system tray) and call `/chat` again to see the 502. Set `MODEL_TIMEOUT_S=0.5` in `.env` and restart to see a 504.

Live test against the real model: `AEGIS_LIVE_MODEL=1 uv run pytest -q -k live`

## Prerequisites to study

1. **How an LLM produces text.** Tokens, prompt vs completion, autoregressive decoding, why output length drives latency. [Hugging Face LLM course — How do Transformers work?](https://huggingface.co/learn/llm-course/chapter1/4)
2. **Tokenization.** Why "tokens" ≠ words, and why cost is billed per token. [OpenAI tokenizer demo](https://platform.openai.com/tokenizer), [HF — Tokenizers](https://huggingface.co/learn/llm-course/chapter2/4)
3. **Ollama's API.** [`/api/chat` reference](https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion), and the `num_predict` option and `keep_alive`.
4. **Async HTTP clients and connection pooling.** Why one shared client, not one per request. [HTTPX async docs](https://www.python-httpx.org/async/) (httpx2 has the same API)
5. **Timeouts.** Connect vs read timeouts, and why every network call needs one. [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/)
6. **502 vs 503 vs 504.** Gateway semantics. [MDN 502](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/502), [MDN 504](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/504)
7. **Test doubles.** Faking an external service at the transport layer. [HTTPX MockTransport](https://www.python-httpx.org/advanced/transports/#mock-transports)
8. **LLM serving landscape.** Ollama vs vLLM vs hosted APIs: throughput, batching, cost. [vLLM docs — intro](https://docs.vllm.ai/en/latest/)

## Review checklist — can you explain…

- [ ] Why rate limiting runs *before* the model call, and which test proves a 429 costs nothing?
- [ ] Why `httpx2.AsyncClient` is created once in the lifespan and not inside `generate()`?
- [ ] Why the connect timeout (2 s) is much shorter than the overall timeout (60 s)?
- [ ] Why 502 for "Ollama down", 504 for "too slow", and 503 only for "Redis down"?
- [ ] How Starlette picks `model_timeout` over `model_unavailable` for a `ReadTimeout`, when both handlers match (hint: the exception's class hierarchy)?
- [ ] What `num_predict` protects against, and why output tokens cost more latency than input tokens?
- [ ] Why `/ready` doesn't check Ollama?
- [ ] How `MockTransport` lets the tests run without a model, and what the live test adds?
- [ ] What happened on the first live run (a 60 s cold start → 504), and two ways to fix it?

## Interview angle

"How do you put an LLM behind an API safely?" Check everything cheap first (auth, limits, validation), cap output tokens, time-box the call, map backend failures to distinct gateway codes, and measure tokens per request, because tokens are the cost. Mention the cold-start 504 you hit, and how you'd handle it in production (warm pool, keep-alive, readiness gating on model load).
