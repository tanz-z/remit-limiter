"""
Unit tests using fakeredis, so these run without a real Redis instance.
Run with: pytest -v
"""
import asyncio
import time
import pytest
import pytest_asyncio
import fakeredis.aioredis

from app.limiters.token_bucket import TokenBucketLimiter
from app.limiters.sliding_window import SlidingWindowLimiter


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


# ---- Token bucket ----------------------------------------------------

@pytest.mark.asyncio
async def test_token_bucket_allows_up_to_capacity(redis_client):
    limiter = TokenBucketLimiter(redis_client)
    for _ in range(5):
        result = await limiter.check("client-a", capacity=5, refill_rate=1)
        assert result.allowed

    # bucket is now empty
    result = await limiter.check("client-a", capacity=5, refill_rate=1)
    assert not result.allowed
    assert result.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_token_bucket_refills_over_time(redis_client):
    limiter = TokenBucketLimiter(redis_client)
    # drain the bucket
    for _ in range(3):
        await limiter.check("client-b", capacity=3, refill_rate=10)  # fast refill
    denied = await limiter.check("client-b", capacity=3, refill_rate=10)
    assert not denied.allowed

    await asyncio.sleep(0.2)  # at 10 tokens/sec, ~2 tokens should regenerate

    allowed = await limiter.check("client-b", capacity=3, refill_rate=10)
    assert allowed.allowed


@pytest.mark.asyncio
async def test_token_bucket_isolates_clients(redis_client):
    limiter = TokenBucketLimiter(redis_client)
    for _ in range(5):
        await limiter.check("client-c", capacity=5, refill_rate=1)
    # a different client should be unaffected
    result = await limiter.check("client-d", capacity=5, refill_rate=1)
    assert result.allowed


# ---- Sliding window ----------------------------------------------------

@pytest.mark.asyncio
async def test_sliding_window_allows_up_to_limit(redis_client):
    limiter = SlidingWindowLimiter(redis_client)
    for _ in range(4):
        result = await limiter.check("client-e", window_seconds=1, window_limit=4)
        assert result.allowed

    result = await limiter.check("client-e", window_seconds=1, window_limit=4)
    assert not result.allowed


@pytest.mark.asyncio
async def test_sliding_window_expires_old_entries(redis_client):
    limiter = SlidingWindowLimiter(redis_client)
    for _ in range(2):
        await limiter.check("client-f", window_seconds=0.3, window_limit=2)
    denied = await limiter.check("client-f", window_seconds=0.3, window_limit=2)
    assert not denied.allowed

    await asyncio.sleep(0.35)  # window has fully slid past

    allowed = await limiter.check("client-f", window_seconds=0.3, window_limit=2)
    assert allowed.allowed


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_exceed_limit(redis_client):
    """
    The whole point of doing this in Lua server-side: fire many concurrent
    requests and confirm the count of allowed ones never exceeds the limit,
    even though they're all racing each other.
    """
    limiter = TokenBucketLimiter(redis_client)
    results = await asyncio.gather(*[
        limiter.check("client-g", capacity=10, refill_rate=0.001)
        for _ in range(50)
    ])
    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == 10
