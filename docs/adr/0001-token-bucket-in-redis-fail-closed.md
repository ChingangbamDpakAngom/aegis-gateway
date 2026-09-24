# ADR-001: Token-bucket rate limiting in Redis Lua, failing closed

**Status:** Accepted · **Date:** 2026-09-25

## Context

The gateway must limit how often each client can call it, and the limit must hold when several gateway processes run at once. A counter kept in one process's memory can't do that, because each process would keep its own count.

## Decision

- **Token bucket.** Each client has a bucket of `capacity` tokens that refills at `refill_rate` tokens per second, and each request spends one token. This allows short bursts up to `capacity` while holding the long-run rate at `refill_rate`. A fixed window ("10 per minute") lets through double traffic at window edges. A sliding log is exact but stores every timestamp.
- **State in Redis, logic in one Lua script.** Read, refill, spend and write happen inside a single `EVALSHA`. Redis runs a script without interleaving other commands, so two concurrent requests can't both spend the last token (no read-modify-write race).
- **Redis's clock (`TIME`), not the caller's.** Gateway servers' clocks drift apart. Taking "now" from Redis gives every instance the same clock.
- **Fail closed.** If Redis is unreachable or slow (0.5 s timeout), `/chat` returns `503` instead of letting traffic through unlimited. For a gateway whose job is protection, an outage that blocks requests is safer than one that silently removes the protection.

## Consequences

- Redis is a hard dependency: a Redis outage stops `/chat`. That's accepted. It's mitigated later with `/ready` (Phase 2) and could be mitigated further with Redis replication.
- The 429 response can say exactly when to retry (`Retry-After`), because the script computes the wait.
- Idle buckets expire once they would have refilled completely, so memory stays bounded by active clients.
