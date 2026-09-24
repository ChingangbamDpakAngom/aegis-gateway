# ADR-002: Identify clients by hashed API keys

**Status:** Accepted · **Date:** 2026-09-25

## Context

In v0.1 the client said who it was in the body (`"user_id": "..."`). The rate limit was keyed on that value, so anyone could send a new `user_id` with every request and never be limited. A limit keyed on something the caller controls isn't a limit.

## Decision

- **Clients authenticate with an API key** in the `X-API-Key` header. The gateway looks up the key and takes the client's identity from the stored record, never from the request body. The body is now just `{"message": ...}`, and unknown fields are rejected.
- **Only a SHA-256 hash of each key is stored** (`apikey:<hash>` in Redis). A leaked database dump doesn't reveal usable keys. Keys are 256 random bits (`secrets.token_urlsafe(32)`), so a fast unsalted hash is safe here. Slow, salted hashes like bcrypt/argon2 are for low-entropy secrets such as passwords.
- **Limits belong to the client, not the key.** The bucket is keyed on `client_id`. Several keys for one client share a bucket, so rotating keys doesn't reset the allowance. A key record can carry its own `capacity` / `refill_per_s`; otherwise the defaults from config apply.
- **API keys, not JWTs.** This is service-to-service traffic with no user login flow. A JWT's advantages (stateless verification, embedded claims) matter less when the gateway already hits Redis on every request for rate limiting, and an opaque key can be revoked instantly by deleting it.
- **One error shape.** Every failure returns `{"error": {"code", "message"}}` with a stable, machine-readable `code` (`missing_api_key`, `invalid_api_key`, `invalid_request`, `rate_limited`, `backend_unavailable`).

## Consequences

- Each request costs two Redis round trips (key lookup + bucket). Fine at this scale. They could be merged into one Lua script if latency ever matters.
- Keys are created with a CLI (`python -m gateway.keys`). There's no revoke command yet: delete the `apikey:<hash>` entry. Add one, plus key listing, when there's an admin surface.
- Message length is capped at `MAX_MESSAGE_CHARS`, but only after the body is parsed. The raw body size should also be capped at the reverse proxy when deployed.
