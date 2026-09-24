# Phase 1 — Identity & request contract

**Goal:** the gateway decides who a caller is, not the caller. Every request has a strict shape and every error has one format.

## What changed and why

| Before | After | Where |
|---|---|---|
| Identity = `user_id` in the body, so anyone could fake a fresh one | Identity = API key in the `X-API-Key` header, looked up in Redis | `api/main.py` → `authenticate()` |
| — | Keys generated with `secrets`, stored only as a SHA-256 hash | `src/gateway/keys.py` |
| One global limit | Per-key `capacity` / `refill_per_s`, falling back to config defaults | `main.py` → `rate_limit()` |
| Rate limiting called inside the route | A chain of FastAPI dependencies: `chat` ← `rate_limit` ← `authenticate` | `main.py` |
| Any message size, extra fields silently ignored | `message` 1–4000 chars, unknown fields → 422 | `api/schemas.py` |
| Mixed error bodies (`{"detail": ...}`, FastAPI's default 422 list) | Always `{"error": {"code", "message"}}` | `main.py` → exception handlers |

## The request, step by step

```
POST /chat  (X-API-Key: aeg_...)
  │
  ├─ authenticate()   no header → 401 missing_api_key
  │                   hash(key) not in Redis → 401 invalid_api_key
  │                   → client record {client_id, capacity?, refill_per_s?}
  ├─ rate_limit()     token bucket keyed on client_id → 429 rate_limited + Retry-After
  ├─ ChatRequest      body validation → 422 invalid_request (+ details per field)
  └─ chat()           200 {"echo", "client_id"}

Any Redis failure anywhere → 503 backend_unavailable (fail closed)
```

FastAPI resolves `Depends(...)` before calling the route. A dependency that raises stops the request right there, so `chat()` only ever runs for an authenticated client that's within its limit.

## Try it yourself

```bash
docker compose up -d
uv run python -m gateway.keys demo --capacity 3      # prints aeg_... once — copy it
uv run uvicorn gateway.api.main:app --reload
curl -X POST localhost:8000/chat -H "X-API-Key: <your key>" -H "Content-Type: application/json" -d '{"message":"hi"}'
curl -X POST localhost:8000/chat -H "Content-Type: application/json" -d '{"message":"hi"}'   # 401
docker compose exec redis redis-cli --scan --pattern 'apikey:*'   # only hashes are stored
```

In `/docs`, click **Authorize** to paste the key once. That button exists because of `APIKeyHeader`.

## Prerequisites to study

1. **Authentication vs authorisation.** Who you are vs what you may do. This phase does authentication (plus per-client limits).
2. **API keys vs JWT vs OAuth2.** When each fits. [OWASP REST Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html)
3. **Hashing vs encryption; why passwords need slow hashes but random API keys don't.** [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) (read the "why" sections)
4. **Python `secrets` and `hashlib`.** [secrets](https://docs.python.org/3/library/secrets.html), [hashlib](https://docs.python.org/3/library/hashlib.html)
5. **FastAPI dependencies and security utilities.** [Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/), [Sub-dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/sub-dependencies/), [Security](https://fastapi.tiangolo.com/tutorial/security/)
6. **Pydantic v2 validation.** [Fields](https://docs.pydantic.dev/latest/concepts/fields/), [Model config (`extra="forbid"`)](https://docs.pydantic.dev/latest/api/config/)
7. **HTTP status codes.** 401 vs 403 vs 422 vs 429 vs 503, and the `Retry-After` header. [MDN HTTP status](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status)
8. **API error design.** Stable machine-readable codes plus human messages. Compare with [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457).

## Review checklist — can you explain…

- [ ] Why a limit keyed on the body's `user_id` wasn't really a limit?
- [ ] Why we store `sha256(key)` and how the lookup still works without the original key?
- [ ] Why SHA-256 is fine for API keys but wrong for passwords?
- [ ] Why two keys for the same `client_id` share one bucket (what attack does that prevent)?
- [ ] The order FastAPI runs `authenticate` → `rate_limit` → `chat`, and what happens when one raises?
- [ ] Why `GatewayError` subclasses `HTTPException`, and how one handler formats both our errors and FastAPI's 404s?
- [ ] What `extra="forbid"` catches that the default wouldn't?
- [ ] Why 401 (not 403) for a bad key, and 503 (not 500) when Redis is down?
- [ ] What the `ponytail:` comment in `schemas.py` warns about, and where the real fix lives?

## Interview angle

"How do you secure and meter a public API?" API keys stored hashed, identity decided by the server, per-client limits, strict input validation, consistent errors. You can point to the tests that prove each one.
