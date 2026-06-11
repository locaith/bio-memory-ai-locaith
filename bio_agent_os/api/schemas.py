"""
Request schemas with input bounds for the public API.

Every mutating endpoint validates its body through these models so that
oversized payloads, runaway ``top_k``/``chunk_size`` values, and malformed
context fields are rejected with a 422 before touching memory stores.
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


class MemoryContext(BaseModel):
    task_id: Optional[str] = Field(default=None, max_length=128)
    workspace_id: Optional[str] = Field(default=None, max_length=128)
    project_version: Optional[str] = Field(default=None, max_length=128)
    mode: str = Field(default="implement", max_length=32)
    stress_state: str = Field(default="normal", max_length=32)
    risk_level: str = Field(default="medium", max_length=32)


class ChatRequest(MemoryContext):
    message: str = Field(max_length=20_000)
    # source_refs entries are opaque provenance markers; the OpenClaw plugin
    # sends objects, the SDK sends strings.
    source_refs: Optional[List[Any]] = Field(default=None, max_length=50)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be empty")
        return stripped


class IngestRequest(MemoryContext):
    text: str = Field(min_length=1, max_length=1_000_000)
    chunk_size: int = Field(default=2000, ge=100, le=20_000)
    source: str = Field(default="ingest", max_length=128)
    observation_type: str = Field(default="ingest", max_length=64)
    source_refs: Optional[List[Any]] = Field(default=None, max_length=50)


class RetrieveRequest(MemoryContext):
    query: str = Field(max_length=20_000)
    top_k: int = Field(default=5, ge=1, le=50)
    prefer_exception: Optional[bool] = None

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be empty")
        return stripped


class RevalidationResolveRequest(BaseModel):
    fact_kind: str = Field(min_length=1, max_length=128)
    fact_value: str = Field(min_length=1, max_length=4096)
    workspace_id: Optional[str] = Field(default=None, max_length=128)
    project_version: Optional[str] = Field(default=None, max_length=128)
    reviewer: str = Field(default="human", max_length=128)


class ApprovalDecisionRequest(BaseModel):
    reviewer: str = Field(default="human", max_length=128)
