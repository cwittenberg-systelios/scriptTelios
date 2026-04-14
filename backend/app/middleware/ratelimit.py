"""
O1 — Rate Limiting Middleware

Begrenzt Anzahl der Requests pro User pro Zeitfenster. Schützt gegen
Missbrauch und übermäßige LLM-Auslastung. Ohne externe Library —
einfaches In-Memory-Token-Bucket pro User.

Limits konfigurierbar via Settings:
    RATE_LIMIT_REQUESTS_PER_HOUR: int = 100
    RATE_LIMIT_PARALLEL_JOBS: int = 5
"""
import time
from collections import defaultdict, deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings


class _UserLimiter:
    """Sliding-Window pro User. In-Memory, thread-safe via Lock."""
    def __init__(self):
        self._windows: dict = defaultdict(deque)
        self._lock = Lock()

    def allow(self, user: str, max_per_hour: int) -> tuple[bool, int]:
        """Prüft ob User noch Requests übrig hat. Returns (allowed, remaining)."""
        now = time.time()
        cutoff = now - 3600
        with self._lock:
            window = self._windows[user]
            # Alte Einträge entfernen
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= max_per_hour:
                return False, 0
            window.append(now)
            return True, max_per_hour - len(window)


_limiter = _UserLimiter()

# Pfade ohne Rate-Limiting
_SKIP_PATHS = {"/api/health", "/health", "/metrics", "/favicon.ico"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # OPTIONS-Preflights immer durchlassen (zaehlt nicht zum Limit)
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path in _SKIP_PATHS or not getattr(settings, "RATE_LIMIT_ENABLED", True):
            return await call_next(request)

        # User aus Auth-Header (gleicher Key wie auth.py setzt)
        user = request.headers.get("X-Systelios-User", "") or "anonymous"
        max_per_hour = getattr(settings, "RATE_LIMIT_REQUESTS_PER_HOUR", 100)

        allowed, remaining = _limiter.allow(user, max_per_hour)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit erreicht: max {max_per_hour} Requests/Stunde",
                    "retry_after_seconds": 3600,
                },
                headers={
                    "Retry-After": "3600",
                    "X-RateLimit-Limit": str(max_per_hour),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_per_hour)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
