-- Token bucket. Runs atomically inside Redis, so concurrent requests
-- can never spend the same token.
-- KEYS[1] = bucket key, ARGV[1] = capacity, ARGV[2] = refill rate (tokens/second)
-- Returns {allowed (1|0), milliseconds until the next token}

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])

-- Redis's own clock, not the caller's: every gateway instance agrees on "now".
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

-- A brand-new client starts with a full bucket.
if tokens == nil then
    tokens = capacity
    last_refill = now
end

-- Refill for the time that has passed, never above capacity.
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
local retry_ms = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
else
    retry_ms = math.ceil((1 - tokens) / refill_rate * 1000)
end

redis.call('HSET', key, 'tokens', tokens, 'last_refill', now)
-- An idle bucket refills completely in capacity/refill_rate seconds; after that it's safe to forget.
redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) + 1)
return {allowed, retry_ms}
