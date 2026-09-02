"""
Thin wrapper around redis-py's async client.

Why a wrapper at all: we want ONE shared connection pool for the whole app
(not a new connection per-request, which would tank latency), and we want
short, explicit timeouts so a Redis outage fails fast instead of hanging
requests for the default multi-second TCP timeout.
"""
import redis.asyncio as redis
from app.config import settings

_pool: redis.ConnectionPool | None = None
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _pool, _client
    if _client is None:
        _pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_socket_connect_timeout,
            max_connections=100,
        )
        _client = redis.Redis(connection_pool=_pool)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
