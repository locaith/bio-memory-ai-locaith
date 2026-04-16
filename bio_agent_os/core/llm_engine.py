"""
core/llm_engine.py — LLM Abstraction Layer.

Giao tiếp với bất kỳ LLM Backend nào (Gemini, Ollama, vLLM, OpenAI-compatible).
Đây là "CPU" của hệ thống — chỉ xử lý, không lưu trữ.
"""

import json
import asyncio
from typing import Optional, Type, Any
from pydantic import BaseModel


class LLMEngine:
    """
    Abstraction layer for LLM communication.
    
    Supports multiple backends:
      - "gemini"  : Google Gemini via google-genai SDK
      - "ollama"  : Local Ollama server
      - "openai"  : Any OpenAI-compatible API (vLLM, LM Studio, etc.)
    
    Usage:
        engine = LLMEngine(backend="gemini", model_id="gemini-3-flash-preview")
        result = await engine.generate("Hello world")
        structured = await engine.generate_structured("Analyze this", schema=MyModel)
    """

    def __init__(
        self, 
        backend: str = "gemini",
        model_id: str = "gemini-3-flash-preview",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
    ):
        self.backend = backend.lower()
        self.model_id = model_id
        self.api_key = api_key
        self.base_url = base_url or self._default_base_url()
        self.temperature = temperature
        self._client = None
        self._init_client()

    def _default_base_url(self) -> str:
        if self.backend == "ollama":
            return "http://localhost:11434"
        elif self.backend == "openai":
            return "http://localhost:8000/v1"
        return ""

    def _init_client(self):
        """Initialize the underlying LLM client based on backend type."""
        if self.backend == "gemini":
            try:
                from google import genai
                if self.api_key:
                    self._client = genai.Client(api_key=self.api_key)
                else:
                    self._client = genai.Client()
            except Exception as e:
                print(f"[LLMEngine] Warning: Gemini client init failed: {e}")
                self._client = None

        elif self.backend == "ollama":
            # Ollama uses HTTP REST API — no special SDK needed
            self._client = "ollama"

        elif self.backend == "openai":
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self.api_key or "not-needed",
                    base_url=self.base_url,
                )
            except ImportError:
                print("[LLMEngine] Warning: openai package not installed. pip install openai")
                self._client = None

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    # ─── Core Generation ─────────────────────────────────────

    async def generate(self, prompt: str, temperature: Optional[float] = None) -> str:
        """Generate a text response from the LLM."""
        temp = temperature if temperature is not None else self.temperature

        if self.backend == "gemini":
            return await self._generate_gemini(prompt, temp)
        elif self.backend == "ollama":
            return await self._generate_ollama(prompt, temp)
        elif self.backend == "openai":
            return await self._generate_openai(prompt, temp)
        else:
            raise ValueError(f"Unknown backend: {self.backend}")

    async def generate_structured(
        self, prompt: str, schema: Type[BaseModel], temperature: float = 0.1
    ) -> dict:
        """Generate a structured JSON response conforming to a Pydantic schema."""

        if self.backend == "gemini":
            return await self._structured_gemini(prompt, schema, temperature)
        else:
            # Fallback: ask LLM to respond in JSON and parse
            return await self._structured_fallback(prompt, schema, temperature)

    # ─── Gemini Backend ───────────────────────────────────────

    async def _generate_gemini(self, prompt: str, temp: float) -> str:
        res = await self._client.aio.models.generate_content(
            model=self.model_id,
            contents=prompt,
            config={"temperature": temp},
        )
        return res.text

    async def _structured_gemini(
        self, prompt: str, schema: Type[BaseModel], temp: float
    ) -> dict:
        res = await self._client.aio.models.generate_content(
            model=self.model_id,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
                "temperature": temp,
            },
        )
        return json.loads(res.text)

    # ─── Ollama Backend ───────────────────────────────────────

    async def _generate_ollama(self, prompt: str, temp: float) -> str:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": self.model_id,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temp},
            }
            async with session.post(
                f"{self.base_url}/api/generate", json=payload
            ) as resp:
                data = await resp.json()
                return data.get("response", "")

    # ─── OpenAI-Compatible Backend ────────────────────────────

    async def _generate_openai(self, prompt: str, temp: float) -> str:
        response = await self._client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "user", "content": prompt}],
            temperature=temp,
        )
        return response.choices[0].message.content

    # ─── Structured Fallback (for non-Gemini backends) ────────

    async def _structured_fallback(
        self, prompt: str, schema: Type[BaseModel], temp: float
    ) -> dict:
        schema_json = json.dumps(schema.model_json_schema(), indent=2, ensure_ascii=False)
        wrapped_prompt = f"""{prompt}

QUAN TRỌNG: Trả lời CHÍNH XÁC bằng JSON theo schema sau (KHÔNG thêm ký tự nào ngoài JSON):
{schema_json}
"""
        raw = await self.generate(wrapped_prompt, temperature=temp)
        # Try to extract JSON from response
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)
