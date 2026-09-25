# ADR-006: Per-client exact-match answer cache; semantic cache built but off by default

**Status:** Accepted · **Date:** 2026-09-25

## Context

Every `/chat` miss costs 2–11 s of LLM time and tokens. Clients often repeat questions. A cache in front of the model makes repeats nearly free. A *semantic* cache, which also reuses answers for questions that mean the same thing, promises more hits, but it can return an answer to a different question.

## Decision

- **Exact cache, on by default.** The key is `cache:{client_id}:{sha256(model + message with whitespace collapsed)}`, the value is `{reply, model}`, and the Redis TTL is `CACHE_TTL_S` (1 h). Including the model in the key means changing `MODEL` never serves another model's answers.
- **Per client, never shared.** A prompt or its answer can contain one client's private data. A global cache would leak it to anyone asking the same or a similar question, so caches are namespaced by `client_id`. This costs hit rate, and that's accepted.
- **Placed after the guard, before the model.** Cached answers are served only for messages that pass *today's* guard. The rate limit still applies to hits, because hits still cost Redis and CPU.
- **Hits are visible**: `"cache": "exact" | "semantic" | "miss"` in the response and log line, `usage` of 0 tokens on a hit, and `aegis_cache_lookups_total{result}` for hit rate.
- **Semantic cache implemented, but `SEMANTIC_CACHE=false` by default.** When on, the message is embedded with Ollama (`all-minilm`, 384 dimensions). The nearest earlier question is found with a **Redis 8 vector set** (`VADD`/`VSIM`, an HNSW index built into Redis, so there's no extra vector database), and its answer is reused if cosine ≥ `SIMILARITY_THRESHOLD` (0.95). Redis moved from 7.4 to 8.2 for this.
- If the embedding model fails, the semantic lookup counts as a miss (`result="error"`) instead of failing `/chat`. The cache is an optimisation, not a dependency.

## Evaluation: why semantic caching is off

`scripts/eval_semantic_cache.py` uses 12 same-meaning pairs (a hit is right) and 12 pairs on the same topic with a different meaning (a hit is a **wrong answer**):

| Threshold | all-minilm: correct hits | all-minilm: wrong hits | nomic-embed-text: correct | nomic-embed-text: wrong |
|---|---|---|---|---|
| 0.80 | 12/12 | 3/12 | 12/12 | 4/12 |
| 0.85 | 10/12 | 2/12 | 11/12 | 2/12 |
| 0.90 | 8/12 | 1/12 | 10/12 | 1/12 |
| 0.95 | 5/12 | 1/12 | 7/12 | 1/12 |
| 0.98 | 0/12 | 1/12 | 1/12 | 1/12 |

"Convert 10 miles to kilometres" vs "Convert 10 kilometres to miles" scores **0.993** (0.990 with the larger model), higher than every real paraphrase. Embeddings capture *what a text is about*, not word order or which number goes where. No threshold gives useful hits with zero wrong answers. The end-to-end run confirmed it: with the semantic cache on, "Convert 10 kilometres to miles" was served the cached miles→kilometres answer in 119 ms.

For a cache, a wrong hit is far worse than a miss: a miss costs seconds, a wrong hit costs trust. So the exact cache is the default. The semantic cache is available for domains where near-duplicates really are interchangeable (FAQ bots, for example), after running the eval on that domain's own pairs.

## Measured (laptop, llama3.2 on Ollama)

| Request | Latency | Tokens |
|---|---|---|
| Miss (model cold) | 11.4 s | full |
| Miss (model warm) | ~2 s | full |
| Exact hit | 11 ms | 0 |

## Consequences

- Answers can be up to `CACHE_TTL_S` stale. That's fine for general questions, but wrong for anything time-sensitive, and there's no per-request bypass yet. Add a `"cache": false` request option when a client needs it.
- The model's answers aren't deterministic, so a cached repeat returns the *first* answer every time. That's usually desirable.
- Redis memory grows with unique questions × clients, bounded by the TTL. Vector sets only shrink when entries are found expired or the set idles out (`ponytail:` note in `cache.py`).
- The semantic path adds one embedding call (~20–100 ms) to every miss when enabled.
- Next: a cross-encoder or LLM re-check of semantic hits could fix the word-order problem, at extra latency. Revisit if hit rate matters.
