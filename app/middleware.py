"""
The middleware is the piece that makes this "reusable": drop it onto any
FastAPI app and every request gets rate-limited without touching route code.
"""
import logging
import redis.exceptions
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings, get_client_limits, Algorithm, FailureMode
from app.limiters.token_bucket import TokenBucketLimiter
from app.limiters.sliding_window import SlidingWindowLimiter
from app.redis_client import get_redis

logger = logging.getLogger("rate_limiter")


def _identify_client(request: Request) -> str:
    if settings.identity_source == "api_key":
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"key:{api_key}"
    # fall back to source IP either way (also used when identity_source == "ip")
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Usage:
        app.add_middleware(RateLimiterMiddleware)

    Reads RATE_LIMIT_* env vars (see app/config.py) for algorithm choice,
    default limits, and fail-open/fail-closed behavior.
    """

    def __init__(self, app, exempt_paths: set[str] | None = None):
        super().__init__(app)
        self._exempt_paths = exempt_paths or {"/health", "/docs", "/openapi.json"}
        redis_client = get_redis()
        self._token_bucket = TokenBucketLimiter(redis_client)
        self._sliding_window = SlidingWindowLimiter(redis_client)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self._exempt_paths:
            return await call_next(request)

        client_id = _identify_client(request)
        limits = get_client_limits(client_id)

        try:
            if settings.algorithm == Algorithm.TOKEN_BUCKET:
                result = await self._token_bucket.check(
                    client_id,
                    capacity=limits["capacity"],
                    refill_rate=limits["refill_rate"],
                )
            else:
                result = await self._sliding_window.check(
                    client_id,
                    window_seconds=settings.default_window_seconds,
                    window_limit=limits["window_limit"],
                )
        except (redis.exceptions.RedisError, ConnectionError, TimeoutError) as exc:
            # Redis is down or too slow. This is the fail-open/fail-closed decision point.
            logger.warning("rate limiter backend unavailable, applying %s: %s",
                            settings.failure_mode.value, exc)
            if settings.failure_mode == FailureMode.FAIL_OPEN:
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={"error": "rate limiter unavailable, failing closed"},
            )

        if not result.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate limit exceeded",
                    "retry_after_seconds": result.retry_after_seconds,
                },
                headers={
                    "Retry-After": str(max(1, int(result.retry_after_seconds) + 1)),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        return response
