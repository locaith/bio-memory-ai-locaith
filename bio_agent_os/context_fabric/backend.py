from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from .models import ContextBlock, StorageTier


class ContextMemoryBackend(ABC):
    """Vendor-neutral contract for RAM/NVMe/distributed/NVIDIA adapters."""

    @abstractmethod
    def store_block(self, block: ContextBlock) -> ContextBlock: ...

    @abstractmethod
    def fetch_block(self, block_id: str, tenant_id: str) -> ContextBlock | None: ...

    @abstractmethod
    def promote_tier(self, block_id: str, tenant_id: str, tier: StorageTier) -> ContextBlock: ...

    @abstractmethod
    def evict(self, block_id: str, tenant_id: str) -> bool: ...

    @abstractmethod
    def share(self, block_ids: Iterable[str], tenant_id: str, target_agent_id: str) -> list[str]: ...
