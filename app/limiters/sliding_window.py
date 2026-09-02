"""
Sliding window algorithm (exact / "sliding log" variant).

Each client has a Redis sorted set (ZSET) where each request is stored as a
member scored by its timestamp. To check a request we:
  1. Drop entries older than `now - window_seconds` (they've slid out of the window)
  2. Count what's left
  3. If under the limit, add the new request's timestamp and allow it

This is more memory-hungry than a fixed-window counter (one entry per
request, not just an integer), but it doesn't have the fixed-window's
boundary problem, where a client can send `limit` requests right at the end
of one window and `limit` more right at the start of the next, doubling
their effective rate for a brief moment.

As with the token bucket, all three steps run in one Lua script so
concurrent requests from multiple app instances can't race each other into
both reading "9 of 10 used" and both being allowed through.
"""
import time
import uuid
import redis.asyncio as redis
from app.limiters.base import LimitResult

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

local window_start = now - window

redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

local count = redis.call('ZCARD', key)

local allowed = 0
if count < limit then
    redis.call('ZADD', key, now, member)
    allowed = 1
    count = count + 1
end

redis.call('EXPIRE', key, math.ceil(window) + 1)

return {allowed, count}
"""


class SlidingWindowLimiter:
    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client
        self._script = self._redis.register_script(_SLIDING_WINDOW_LUA)

    async def check(
        self,
        client_id: str,
        window_seconds: int,
        window_limit: int,
    ) -> LimitResult:
        key = f"rl:sw:{client_id}"
        now = time.time()
        # unique member so simultaneous requests at the same float-timestamp
        # don't collide and get deduped by the ZSET
        member = f"{now}:{uuid.uuid4().hex[:8]}"

        allowed, count = await self._script(
            keys=[key],
            args=[now, window_seconds, window_limit, member],
        )

        remaining = max(0, window_limit - int(count))
        retry_after = 0.0 if allowed else window_seconds / max(window_limit, 1)

        return LimitResult(
            allowed=bool(allowed),
            remaining=remaining,
            retry_after_seconds=round(retry_after, 3),
            limit=window_limit,
        )
