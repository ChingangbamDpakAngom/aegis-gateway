# Phase 5 — Answer cache

**Goal:** stop paying seconds and tokens for questions the gateway has already answered, without ever serving a wrong or someone else's answer.

## What changed and why

| Before | After | Where |
|---|---|---|
| Every allowed message went to the LLM | Repeat questions are answered from Redis in ~11 ms with 0 tokens | `core/cache.py`, `api/main.py` → `chat()` |
| — | Cache is per client (`cache:{client_id}:{sha256}`) with a 1 h TTL | `cache.py` |
| — | Optional semantic cache: embeddings (Ollama `all-minilm`) + Redis 8 vector sets | `cache.py` → `lookup()` / `store()` |
| — | An eval script that picks the similarity threshold, and showed semantic caching is unsafe here | `scripts/eval_semantic_cache.py` |
| Response had no cache info | `"cache": "exact" \| "semantic" \| "miss"`; the log line has `cache` and `cache_similarity`; metric `aegis_cache_lookups_total{result}` | `main.py`, `observability.py` |
| Redis 7.4 | Redis 8.2 (vector sets are built in), in both compose and CI | `docker-compose.yml`, `ci.yml` |

## The request, step by step

```
POST /chat
  ├─ authenticate → rate_limit → validate → guard        401 / 429 / 422 / 400
  ├─ cache.lookup
  │     GET cache:{client}:{sha256(model + normalised message)}  → hit: 200, "cache": "exact", 0 tokens
  │     if SEMANTIC_CACHE:
  │        embed message (Ollama /api/embed)
  │        VSIM semcache:{client}:… → nearest earlier question + score
  │        cosine = 2·score − 1 ≥ 0.95 and its answer still cached → 200, "cache": "semantic"
  ├─ generate() → Ollama                                  502 / 504
  └─ cache.store: SET answer EX 3600 (+ VADD the vector)  → 200, "cache": "miss"
```

## Try it yourself

```bash
docker compose up -d                       # now Redis 8.2; re-create your API key afterwards
uv run python -m gateway.keys demo
uv run uvicorn gateway.api.main:app
# ask the same thing twice and compare the time and the "cache" field
time curl -s -X POST localhost:8000/chat -H "X-API-Key: <key>" -H "Content-Type: application/json" -d '{"message":"What is Redis in one sentence?"}'
time curl -s -X POST localhost:8000/chat -H "X-API-Key: <key>" -H "Content-Type: application/json" -d '{"message":"What is Redis in one sentence?"}'
docker compose exec redis redis-cli --scan --pattern 'cache:*'
curl -s localhost:8000/metrics | grep aegis_cache
```

To see the semantic cache fail: `ollama pull all-minilm`, set `SEMANTIC_CACHE=true` in `.env`, restart, then ask "Convert 10 miles to kilometres" followed by "Convert 10 kilometres to miles". Run `uv run python scripts/eval_semantic_cache.py` for the full table.

## Measured

| Request | Latency | Tokens |
|---|---|---|
| Miss (model cold) | 11.4 s | full |
| Miss (model warm) | ~2 s | full |
| Exact hit | 11 ms | 0 |

Semantic eval (12 same-meaning pairs, 12 different-meaning pairs, all-minilm): at 0.95, 5/12 correct hits and **1/12 wrong hits**. The wrong one, "10 miles → km" vs "10 km → miles", scores 0.993, above every real paraphrase. A larger embedding model (nomic-embed-text) did the same (0.990). Full table in [ADR-006](../adr/0006-answer-cache.md).

## Prerequisites to study

1. **Caching basics.** Cache-aside, TTL, hit rate, staleness, invalidation. [AWS caching overview](https://aws.amazon.com/caching/), [Redis caching patterns](https://redis.io/docs/latest/develop/use/patterns/)
2. **Hash functions as keys.** Why `sha256(model + normalised text)` is a safe, fixed-size key.
3. **Multi-tenancy and data isolation.** Why a shared LLM cache can leak data between customers. [OWASP LLM02: Sensitive Information Disclosure](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)
4. **Embeddings and cosine similarity.** What a sentence embedding captures and what it throws away. [HF — Sentence embeddings (course ch. 5, semantic search)](https://huggingface.co/learn/llm-course/chapter5/6), [SBERT docs](https://www.sbert.net/)
5. **Approximate nearest neighbours / HNSW.** How a vector index finds "closest" quickly. [Pinecone — HNSW explained](https://www.pinecone.io/learn/series/faiss/hnsw/)
6. **Redis vector sets.** `VADD`, `VSIM`, scores. [Redis docs — vector sets](https://redis.io/docs/latest/develop/data-types/vector-sets/)
7. **Semantic caching for LLMs.** The idea and its risks. [GPTCache README](https://github.com/zilliztech/GPTCache) (read the "how it works" section)
8. **Precision vs recall for caches.** Why a false hit costs more than a miss, and why that pushes the threshold up.

## Review checklist — can you explain…

- [ ] Why the cache key includes the model name and collapses whitespace?
- [ ] Why the cache is per client, and what exactly would leak if it were global?
- [ ] Why the cache lookup is *after* the guard and *after* the rate limit?
- [ ] What TTL trades off, and one kind of question where a 1 h cache is wrong?
- [ ] Why hits report `usage` of 0 tokens, and how you'd compute a hit rate from `/metrics`?
- [ ] What an embedding is, what cosine similarity measures, and why `2·score − 1`?
- [ ] Why "10 miles → km" and "10 km → miles" score 0.99, and why no threshold fixes it?
- [ ] Why the semantic cache is off by default, and when you'd turn it on?
- [ ] Why an embedding failure is treated as a miss rather than a 502?
- [ ] What bug in redis-py's `vsim()` made `cache.py` call `VSIM` directly, and how the tests caught it?
- [ ] What the `Trade-off:` comment in `store()` warns about?

## Interview angle

"Would you add semantic caching to an LLM app?" Show the numbers: the exact cache turns 2–11 s into 11 ms at zero token cost, and it's always correct. The semantic cache looked like a free win, but the eval found a 0.99-similar pair with the opposite answer, so it's opt-in and has to be validated per domain. Mention tenant isolation: caches are per client so answers can't leak. That's how you judge a trendy technique with data.
