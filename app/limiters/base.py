from dataclasses import dataclass
from typing import Protocol


@dataclass
class LimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: float = 0.0
    limit: int = 0


class RateLimiter(Protocol):
    async def check(self, client_id: str, **limit_kwargs) -> LimitResult:
        """Return whether this client's request should be allowed."""
        ...
