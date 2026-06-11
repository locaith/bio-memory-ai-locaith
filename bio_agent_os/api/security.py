"""
API security: bearer-token authentication and per-client rate limiting.

Configuration comes from environment variables:

- ``BIO_AGENT_API_KEY``: when set, every endpoint except the public ones
  (dashboard, health, OpenAPI docs) requires ``Authorization: Bearer <key>``.
  This key is the *admin* key with unrestricted access.
- ``BIO_AGENT_TENANT_KEYS``: optional comma-separated tenant keys with
  workspace scopes, e.g. ``key-a=ws-alpha|ws-beta,key-b=ws-gamma``. A tenant
  key only reaches data-plane endpoints, restricted to its workspaces.
- ``BIO_AGENT_RATE_LIMIT``: requests per minute per client IP (default 120,
  ``0`` disables rate limiting).
- ``BIO_AGENT_CORS_ORIGINS``: comma-separated allowed origins. Defaults to
  localhost origins only. ``*`` is honoured but disables credentials.

After authentication the middleware stores the caller's workspace scope on
``request.state.workspace_scope``: ``None`` means unrestricted (admin key or
auth disabled), a frozenset of workspace ids means tenant-restricted.
"""

from __future__ import annotations

import math
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, FrozenSet, List, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PUBLIC_PATHS = {"/", "/api/health", "/docs", "/redoc", "/openapi.json"}

RATE_LIMIT_EXEMPT_PATHS = {"/api/health"}

# Safety valve so a scan from many spoofed IPs cannot grow the limiter
# without bound. Hitting it resets all windows, which is acceptable.
MAX_TRACKED_CLIENTS = 10_000


def _parse_tenant_keys(raw: str) -> Dict[str, FrozenSet[str]]:
    tenants: Dict[str, FrozenSet[str]] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, _, workspaces_raw = pair.partition("=")
        key = key.strip()
        workspaces = frozenset(
            workspace.strip() for workspace in workspaces_raw.split("|") if workspace.strip()
        )
        if key and workspaces:
            tenants[key] = workspaces
    return tenants


@dataclass
class APISecurityConfig:
    api_key: Optional[str]
    tenant_keys: Dict[str, FrozenSet[str]]
    rate_limit_per_minute: int
    cors_origins: List[str]
    cors_allow_credentials: bool

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key or self.tenant_keys)

    @classmethod
    def from_env(cls) -> "APISecurityConfig":
        api_key = (os.getenv("BIO_AGENT_API_KEY") or "").strip() or None
        tenant_keys = _parse_tenant_keys(os.getenv("BIO_AGENT_TENANT_KEYS", ""))
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
            tenant_keys=tenant_keys,
            rate_limit_per_minute=max(0, rate_limit),
            cors_origins=origins,
            cors_allow_credentials="*" not in origins,
        )


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: APISecurityConfig):
        super().__init__(app)
        self._config = config

    async def dispatch(self, request: Request, call_next):
        request.state.workspace_scope = None
        if not self._config.auth_enabled or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            return self._unauthorized()

        if self._config.api_key and secrets.compare_digest(token, self._config.api_key):
            return await call_next(request)

        # Compare against every tenant key (no early exit on mismatch) so
        # timing does not reveal which keys exist.
        matched_scope: Optional[FrozenSet[str]] = None
        for tenant_key, scope in self._config.tenant_keys.items():
            if secrets.compare_digest(token, tenant_key):
                matched_scope = scope
        if matched_scope is not None:
            request.state.workspace_scope = matched_scope
            return await call_next(request)
        return self._unauthorized()

    @staticmethod
    def _unauthorized() -> JSONResponse:
        return JSONResponse(
            {"detail": "Invalid or missing API key."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )


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
