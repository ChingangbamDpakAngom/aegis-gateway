"""Answer cache in Redis: exact match first, then (optionally) similar meaning.

Every cache is per client: one client's answers are never served to another,
because a prompt or its answer can contain that client's private data.
"""

import hashlib
import json

import httpx2
from prometheus_client import Counter

from gateway import config

LOOKUPS = Counter("aegis_cache_lookups_total", "Cache lookups by result", ["result"])


def _digest(message: str) -> str:
    # Whitespace differences don't change the question; the models do change the answer.
    normalised = " ".join(message.split())
    return hashlib.sha256(f"{config.MODEL}|{config.LARGE_MODEL}\n{normalised}".encode()).hexdigest()


def _answer_key(client_id: str, digest: str) -> str:
    return f"cache:{client_id}:{digest}"


def _vector_key(client_id: str) -> str:
    return f"semcache:{client_id}:{config.MODEL}|{config.LARGE_MODEL}:{config.EMBED_MODEL}"


async def _embed(http: httpx2.AsyncClient, text: str) -> list[float]:
    response = await http.post("/api/embed", json={"model": config.EMBED_MODEL, "input": text})
    response.raise_for_status()
    return response.json()["embeddings"][0]


async def lookup(redis, http, client_id: str, message: str) -> tuple[dict | None, list[float] | None]:
    """Return (hit, vector). hit is {"reply", "model", "cache", "similarity"} or None.
    The vector is handed back so store() doesn't embed the same message twice."""
    cached = await redis.get(_answer_key(client_id, _digest(message)))
    if cached:
        LOOKUPS.labels("exact").inc()
        return {**json.loads(cached), "cache": "exact", "similarity": 1.0}, None
    if not config.SEMANTIC_CACHE:
        LOOKUPS.labels("miss").inc()
        return None, None

    try:
        vector = await _embed(http, message)
    except httpx2.HTTPError:
        # A broken embedding model shouldn't take /chat down: treat it as a miss.
        LOOKUPS.labels("error").inc()
        return None, None
    # Raw command: redis-py's vsim() crashes when the set doesn't exist yet (reply is []
    # instead of {element: score}), which is every client's first request.
    reply = await redis.execute_command(
        "VSIM", _vector_key(client_id), "VALUES", len(vector), *vector, "WITHSCORES", "COUNT", 1
    )
    for digest, score in (reply or {}).items():
        similarity = 2 * score - 1  # Redis scores (1 + cosine) / 2; convert back to cosine
        if similarity < config.SIMILARITY_THRESHOLD:
            break
        cached = await redis.get(_answer_key(client_id, digest))
        if cached is None:  # the answer expired; drop its vector too
            await redis.vset().vrem(_vector_key(client_id), digest)
            break
        LOOKUPS.labels("semantic").inc()
        return {**json.loads(cached), "cache": "semantic", "similarity": round(similarity, 4)}, vector
    LOOKUPS.labels("miss").inc()
    return None, vector


async def store(redis, client_id: str, message: str, result: dict, vector: list[float] | None) -> None:
    digest = _digest(message)
    answer = json.dumps({"reply": result["reply"], "model": result["model"]})
    await redis.set(_answer_key(client_id, digest), answer, ex=config.CACHE_TTL_S)
    if vector is not None:
        # ponytail: the vector set only shrinks when answers expire and get looked up, or
        # when the whole set idles out; cap it (VCARD + VREM oldest) if clients cache a lot.
        await redis.vset().vadd(_vector_key(client_id), vector, digest)
        await redis.expire(_vector_key(client_id), config.CACHE_TTL_S)
