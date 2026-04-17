"""
LLM abstraction layer for portable memory operations.
"""

import json
import os
from typing import Optional, Type

from pydantic import BaseModel


class LLMEngine:
    """
    Supported backends:
    - gemini
    - ollama
    - openai
    - anthropic
    - grok

    `openai` is also the generic OpenAI-compatible path for local AI servers,
    LM Studio, vLLM, OpenWebUI, and AI Local setups.
    """

    def __init__(
        self,
        backend: str = "gemini",
        model_id: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
    ):
        self.backend = backend.lower()
        self.model_id = model_id
        self.api_key = api_key or self._default_api_key()
        self.base_url = base_url or self._default_base_url()
        self.temperature = temperature
        self._client = None
        self._init_client()

    @classmethod
    def from_env(cls) -> "LLMEngine":
        backend = os.getenv("LLM_BACKEND", "gemini")
        model_id = os.getenv("MODEL_ID", "gemini-2.5-flash")
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        temperature = float(os.getenv("LLM_TEMPERATURE", "0.3"))
        return cls(
            backend=backend,
            model_id=model_id,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )

    def _default_api_key(self) -> Optional[str]:
        key_map = {
            "gemini": os.getenv("GEMINI_API_KEY"),
            "openai": os.getenv("OPENAI_API_KEY"),
            "anthropic": os.getenv("ANTHROPIC_API_KEY"),
            "grok": os.getenv("XAI_API_KEY"),
            "ollama": os.getenv("OLLAMA_API_KEY"),
        }
        return key_map.get(self.backend)

    def _default_base_url(self) -> str:
        if self.backend == "ollama":
            return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        if self.backend == "openai":
            return os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
        if self.backend == "grok":
            return os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
        return ""

    def _init_client(self):
        if self.backend == "gemini":
            try:
                from google import genai

                self._client = genai.Client(api_key=self.api_key) if self.api_key else genai.Client()
            except Exception as exc:
                print(f"[LLMEngine] Gemini init failed: {exc}")
                self._client = None
            return

        if self.backend in {"openai", "grok"}:
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(
                    api_key=self.api_key or "local-dev-key",
                    base_url=self.base_url,
                )
            except Exception as exc:
                print(f"[LLMEngine] OpenAI-compatible init failed: {exc}")
                self._client = None
            return

        if self.backend == "anthropic":
            try:
                import anthropic

                self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
            except Exception as exc:
                print(f"[LLMEngine] Anthropic init failed: {exc}")
                self._client = None
            return

        if self.backend == "ollama":
            self._client = "ollama"
            return

        raise ValueError(f"Unsupported backend: {self.backend}")

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    async def generate(self, prompt: str, temperature: Optional[float] = None) -> str:
        temp = temperature if temperature is not None else self.temperature

        if self.backend == "gemini":
            return await self._generate_gemini(prompt, temp)
        if self.backend == "ollama":
            return await self._generate_ollama(prompt, temp)
        if self.backend in {"openai", "grok"}:
            return await self._generate_openai_compatible(prompt, temp)
        if self.backend == "anthropic":
            return await self._generate_anthropic(prompt, temp)

        raise ValueError(f"Unsupported backend: {self.backend}")

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[BaseModel],
        temperature: float = 0.1,
    ) -> dict:
        if self.backend == "gemini":
            return await self._structured_gemini(prompt, schema, temperature)
        return await self._structured_fallback(prompt, schema, temperature)

    async def _generate_gemini(self, prompt: str, temp: float) -> str:
        response = await self._client.aio.models.generate_content(
            model=self.model_id,
            contents=prompt,
            config={"temperature": temp},
        )
        return response.text

    async def _structured_gemini(
        self,
        prompt: str,
        schema: Type[BaseModel],
        temp: float,
    ) -> dict:
        response = await self._client.aio.models.generate_content(
            model=self.model_id,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
                "temperature": temp,
            },
        )
        return json.loads(response.text)

    async def _generate_ollama(self, prompt: str, temp: float) -> str:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            payload = {
                "model": self.model_id,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temp},
            }
            async with session.post(f"{self.base_url}/api/generate", json=payload) as response:
                data = await response.json()
                return data.get("response", "")

    async def _generate_openai_compatible(self, prompt: str, temp: float) -> str:
        response = await self._client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "user", "content": prompt}],
            temperature=temp,
        )
        return response.choices[0].message.content or ""

    async def _generate_anthropic(self, prompt: str, temp: float) -> str:
        response = await self._client.messages.create(
            model=self.model_id,
            max_tokens=1200,
            temperature=temp,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts)

    async def _structured_fallback(
        self,
        prompt: str,
        schema: Type[BaseModel],
        temp: float,
    ) -> dict:
        schema_json = json.dumps(schema.model_json_schema(), indent=2, ensure_ascii=False)
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. Do not add markdown fences.\n"
            f"Schema:\n{schema_json}\n"
        )
        raw = await self.generate(wrapped_prompt, temperature=temp)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)
