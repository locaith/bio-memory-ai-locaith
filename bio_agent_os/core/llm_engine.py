"""
LLM abstraction layer for portable memory operations.
"""

import json
import os
import re
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
        model_id: str = "gemini-3-flash-preview",
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
        model_id = os.getenv("MODEL_ID", "gemini-3-flash-preview")
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
            # api.openai.com, not localhost. This defaulted to
            # http://localhost:8000/v1 — presumably for a local
            # OpenAI-compatible server — and the consequence is worse than a
            # confusing 404: `LLM_BACKEND=openai` with a real API key sends that
            # key, in an Authorization header, together with whatever text is
            # being processed, to whatever happens to be listening on port 8000.
            # On this machine that is a PDF OCR backend. It could be anything.
            #
            # A local server is still one environment variable away; pointing at
            # the vendor whose name the backend carries is the safe default.
            return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
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

    async def generate(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        effort: Optional[str] = None,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature
        prompt = self._apply_effort_hint(prompt, effort)

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
        effort: Optional[str] = None,
    ) -> dict:
        prompt = self._apply_effort_hint(prompt, effort)
        if self.backend == "gemini":
            return await self._structured_gemini(prompt, schema, temperature)
        return await self._structured_fallback(prompt, schema, temperature)

    def _apply_effort_hint(self, prompt: str, effort: Optional[str]) -> str:
        if not effort:
            return prompt
        return f"[Adaptive effort hint: {effort}]\n{prompt}"

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
                "options": {
                    "temperature": temp,
                    # Bound the KV cache: long-context models (Gemma 4 reports
                    # 256K) otherwise allocate context at full size per load,
                    # which can exhaust VRAM+RAM and freeze the host.
                    "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "8192")),
                },
            }
            async with session.post(f"{self.base_url}/api/generate", json=payload) as response:
                data = await response.json()
                if response.status != 200:
                    # Fail loudly: a swallowed 429 here once turned a 3-hour
                    # eval into 300 empty predictions.
                    raise RuntimeError(
                        f"Ollama returned HTTP {response.status}: {str(data)[:200]}"
                    )
                text = data.get("response", "")
                if not text and data.get("error"):
                    raise RuntimeError(f"Ollama error: {data['error']}")
                return text

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
        extra_rules = ""
        if schema.__name__ == "CompiledMemory":
            extra_rules = (
                'The "scope" field must be exactly one of: "core", "project", "agent", "user", "session", "organization".\n'
                'The "confidence" field must be a number from 0 to 1.\n'
            )
        elif schema.__name__ == "MemoryLabel":
            extra_rules = (
                'The "importance_score" field must be an integer from 1 to 10.\n'
                'Keep "topic" concise.\n'
            )
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. Do not add markdown fences.\n"
            "Every required field must be present.\n"
            "Use plain JSON values, not prose explanations.\n"
            f"{extra_rules}"
            f"Schema:\n{schema_json}\n"
        )
        last_error: Optional[Exception] = None
        raw = ""
        for attempt in range(2):
            raw = await self.generate(wrapped_prompt, temperature=temp if attempt == 0 else 0.0)
            try:
                candidate = self._extract_json_candidate(raw)
                payload = json.loads(candidate)
                return schema.model_validate(payload).model_dump()
            except Exception as exc:
                last_error = exc
                repair_prompt = self._build_json_repair_prompt(raw, schema_json)
                wrapped_prompt = repair_prompt
        raise ValueError(f"Structured generation failed for {schema.__name__}: {last_error}. Raw output: {raw[:400]}")

    def _extract_json_candidate(self, raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        raise ValueError("No JSON object found in model output.")

    def _build_json_repair_prompt(self, raw: str, schema_json: str) -> str:
        return (
            "Rewrite the following output into one valid JSON object only.\n"
            "Do not explain. Do not add markdown fences. Do not omit required fields.\n\n"
            f"Schema:\n{schema_json}\n\n"
            f"Output to repair:\n{raw}"
        )
