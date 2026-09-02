"""
Central configuration, loaded from environment variables so this works
the same way in local dev, k6 load tests, and (eventually) a real deployment.
"""
import os
from enum import Enum


class Algorithm(str, Enum):
    TOKEN_BUCKET = "token_bucket"
    SLIDING_WINDOW = "sliding_window"


class FailureMode(str, Enum):
    # What to do if Redis is unreachable when we try to check a limit.
    FAIL_OPEN = "fail_open"      # let the request through (favor availability)
    FAIL_CLOSED = "fail_closed"  # reject the request (favor protecting the backend)


class Settings:
    def __init__(self) -> None:
        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        self.algorithm: Algorithm = Algorithm(
            os.getenv("RATE_LIMIT_ALGORITHM", Algorithm.TOKEN_BUCKET.value)
        )
        self.failure_mode: FailureMode = FailureMode(
            os.getenv("RATE_LIMIT_FAILURE_MODE", FailureMode.FAIL_OPEN.value)
        )

        # Default limits, used when a client has no specific override.
        self.default_capacity: int = int(os.getenv("RATE_LIMIT_DEFAULT_CAPACITY", "100"))
        self.default_refill_rate: float = float(os.getenv("RATE_LIMIT_DEFAULT_REFILL_RATE", "50"))  # tokens/sec
        self.default_window_seconds: int = int(os.getenv("RATE_LIMIT_DEFAULT_WINDOW_SECONDS", "1"))
        self.default_window_limit: int = int(os.getenv("RATE_LIMIT_DEFAULT_WINDOW_LIMIT", "100"))

        # How the middleware identifies a "client" for rate-limiting purposes.
        # "api_key" reads the X-API-Key header; "ip" uses the client's source IP.
        self.identity_source: str = os.getenv("RATE_LIMIT_IDENTITY_SOURCE", "api_key")

        # Redis connection timeouts — kept short so a dead Redis fails fast
        # instead of stalling every request behind it.
        self.redis_socket_timeout: float = float(os.getenv("REDIS_SOCKET_TIMEOUT", "0.05"))
        self.redis_socket_connect_timeout: float = float(os.getenv("REDIS_CONNECT_TIMEOUT", "0.05"))


settings = Settings()


# Per-client overrides. In a real system this would come from a DB or config
# service; here it's an in-memory example wired to be easy to swap out.
PER_CLIENT_LIMITS: dict[str, dict] = {
    "premium-client": {"capacity": 1000, "refill_rate": 500, "window_limit": 1000},
    "free-client": {"capacity": 20, "refill_rate": 5, "window_limit": 20},
}


def get_client_limits(client_id: str) -> dict:
    return PER_CLIENT_LIMITS.get(
        client_id,
        {
            "capacity": settings.default_capacity,
            "refill_rate": settings.default_refill_rate,
            "window_limit": settings.default_window_limit,
        },
    )
