"""
Token bucket algorithm.

Each client gets a bucket that holds up to `capacity` tokens and refills at
`refill_rate` tokens/second. Every request costs 1 token. This allows short
bursts up to `capacity` while enforcing a steady average rate over time.

Why a Lua script instead of separate GET/SET calls from Python: two service
instances could both read "5 tokens left" at the same moment, both decide to
allow the request, and both decrement -- letting more requests through than
the limit permits. Redis executes Lua scripts atomically (single-threaded),
so this "check-then-decrement" happens as one indivisible step no matter how
many app instances are hitting Redis concurrently.
"""
import time
import redis.asyncio as redis
from app.limiters.base import LimitResult

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local last_ts = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_ts = now
end

-- refill based on elapsed time since we last touched this bucket
local elapsed = math.max(0, now - last_ts)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, ttl)

return {allowed, tokens}
"""


class TokenBucketLimiter:
    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client
        self._script = self._redis.register_script(_TOKEN_BUCKET_LUA)

    async def check(
        self,
        client_id: str,
        capacity: int,
        refill_rate: float,
        cost: int = 1,
    ) -> LimitResult:
        key = f"rl:tb:{client_id}"
        now = time.time()
        # idle buckets expire after long enough that a fully-drained bucket
        # would have refilled anyway -- keeps Redis memory bounded
        ttl = max(60, int(capacity / max(refill_rate, 0.001)) + 60)

        allowed, remaining = await self._script(
            keys=[key],
            args=[capacity, refill_rate, now, cost, ttl],
        )

        remaining = int(remaining)
        retry_after = 0.0 if allowed else max(0.0, (cost - remaining) / refill_rate)

        return LimitResult(
            allowed=bool(allowed),
            remaining=remaining,
            retry_after_seconds=round(retry_after, 3),
            limit=capacity,
        )
