"""
REST client for remote Bio-Agent OS servers.
"""

from __future__ import annotations

from typing import Any, Optional


class BioAgentRESTClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8055", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._httpx = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _module(self):
        if self._httpx is None:
            try:
                import httpx  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "REST client requires httpx. Install with `pip install bio-agent-os[client]`."
                ) from exc
            import httpx

            self._httpx = httpx
        return self._httpx

    async def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        httpx = await self._module()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=60.0) as client:
            response = await client.request(method, path, json=payload, headers=self._headers())
            response.raise_for_status()
            return response.json()

    async def chat(self, message: str, **context: Any) -> dict[str, Any]:
        return await self._request("POST", "/api/chat", {"message": message, **context})

    async def ingest(self, text: str, **context: Any) -> dict[str, Any]:
        return await self._request("POST", "/api/ingest", {"text": text, **context})

    async def sleep(self) -> dict[str, Any]:
        return await self._request("POST", "/api/sleep", {})

    async def dream(self) -> dict[str, Any]:
        return await self._request("POST", "/api/dream", {})

    async def reflect(self) -> dict[str, Any]:
        return await self._request("GET", "/api/reflect")

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/api/status")

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/health")
