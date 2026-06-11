"""
API security: bearer-token authentication and per-client rate limiting.

Configuration comes from environment variables:

- ``BIO_AGENT_API_KEY``: when set, every endpoint except the public ones
  (dashboard, health, OpenAPI docs) requires ``Authorization: Bearer <key>``.
- ``BIO_AGENT_RATE_LIMIT``: requests per minute per client IP (default 120,
  ``0`` disables rate limiting).
- ``BIO_AGENT_CORS_ORIGINS``: comma-separated allowed origins. Defaults to
  localhost origins only. ``*`` is honoured but disables credentials.
"""

from __future__ import annotations

import math
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PUBLIC_PATHS = {"/", "/api/health", "/docs", "/redoc", "/openapi.json"}

RATE_LIMIT_EXEMPT_PATHS = {"/api/health"}

# Safety valve so a scan from many spoofed IPs cannot grow the limiter
# without bound. Hitting it resets all windows, which is acceptable.
MAX_TRACKED_CLIENTS = 10_000


@dataclass
class APISecurityConfig:
    api_key: Optional[str]
    rate_limit_per_minute: int
    cors_origins: List[str]
    cors_allow_credentials: bool

    @classmethod
    def from_env(cls) -> "APISecurityConfig":
        api_key = (os.getenv("BIO_AGENT_API_KEY") or "").strip() or None
        try:
            rate_limit = int(os.getenv("BIO_AGENT_RATE_LIMIT", "120"))
        except ValueError:
            rate_limit = 120
        raw_origins = os.getenv("BIO_AGENT_CORS_ORIGINS", "")
        origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        if not origins:
            origins = ["http://127.0.0.1:8055", "http://localhost:8055"]
        return cls(
            api_key=api_key,
            rate_limit_per_minute=max(0, rate_limit),
            cors_origins=origins,
            cors_allow_credentials="*" not in origins,
        )


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: APISecurityConfig):
        super().__init__(app)
        self._config = config

    async def dispatch(self, request: Request, call_next):
        if not self._config.api_key or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token or not secrets.compare_digest(token, self._config.api_key):
            return JSONResponse(
                {"detail": "Invalid or missing API key."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    WINDOW_SECONDS = 60.0

    def __init__(self, app, config: APISecurityConfig):
        super().__init__(app)
        self._limit = config.rate_limit_per_minute
        self._buckets: Dict[str, Deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if self._limit <= 0 or request.url.path in RATE_LIMIT_EXEMPT_PATHS:
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        bucket = self._buckets.get(client_ip)
        if bucket is None:
            if len(self._buckets) >= MAX_TRACKED_CLIENTS:
                self._buckets.clear()
            bucket = deque()
            self._buckets[client_ip] = bucket

        cutoff = now - self.WINDOW_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self._limit:
            retry_after = max(1, math.ceil(bucket[0] + self.WINDOW_SECONDS - now))
            return JSONResponse(
                {"detail": "Rate limit exceeded."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        return await call_next(request)
