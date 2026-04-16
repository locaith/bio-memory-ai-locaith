"""
background_jobs/garbage_collector.py — Synaptic Pruning (Thợ Vườn).

Mô phỏng cơ chế cắt tỉa khớp thần kinh:
  - Xoá Raw Data hết hạn TTL trong L1
  - Prune các Vector/Nodes hết hạn trong L2
  - Áp dụng hàm suy giảm thời gian Ebbinghaus: W(t) = W0 * e^(-λt)
"""

import math
import time
from typing import List, Dict, Any

from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


class GarbageCollector:
    """
    Synaptic Pruning Engine — The "Mad Gardener" that cuts dead neural connections.
    
    Operations:
      1. prune_l1() — Remove junk/expired entries from L1 Working Memory
      2. prune_l2() — Remove decayed entries from L2 Semantic Memory
      3. run()      — Full pruning cycle (L1 + L2)
    
    Usage:
        gc = GarbageCollector(l1=l1_mem, l2=l2_mem)
        gc.run()
    """

    def __init__(
        self,
        l1: L1WorkingMemory,
        l2: L2SemanticMemory = None,
        decay_lambda: float = 0.3,
        min_weight: float = 3.0,
    ):
        self.l1 = l1
        self.l2 = l2
        self.decay_lambda = decay_lambda
        self.min_weight = min_weight
        self._log: List[str] = []

    @property
    def logs(self) -> List[str]:
        return list(self._log)

    def clear_logs(self):
        self._log.clear()

    # ─── L1 Pruning (Short-term) ──────────────────────────────

    def prune_l1(self) -> Dict[str, int]:
        """
        Prune L1 Working Memory using TTL + Ebbinghaus time-decay.
        
        Rules:
          1. Entries within TTL (nights_passed <= ttl): Always keep
          2. Entries past TTL + marked as junk: Delete immediately  
          3. Entries past TTL + low decayed weight: Delete (forgotten)
          4. Entries past TTL + high weight: Keep (ready for encoding)
        """
        self._log.append("--- BẮT ĐẦU CẮT TỈA L1 (Synaptic Pruning) ---")
        
        # First, increment nights
        self.l1.increment_nights()
        
        entries = self.l1.get_all()
        initial_count = len(entries)
        survivors = []
        pruned_timestamps = []

        for entry in entries:
            nights = entry.get("nights_passed", 0)
            ttl = entry.get("ttl", 2)
            metadata = entry.get("metadata", {})
            importance = metadata.get("importance_score", 5)
            is_junk = metadata.get("is_junk_or_transient", False)

            # Rule 1: Within TTL → always keep
            if nights <= ttl:
                survivors.append(entry)
                continue

            # Rule 2: Past TTL + junk → delete
            if is_junk:
                pruned_timestamps.append(entry["timestamp"])
                continue

            # Rule 3: Ebbinghaus decay check
            # W(t) = W0 * e^(-λ * (t - ttl))
            w_t = importance * math.exp(-self.decay_lambda * (nights - ttl))

            if w_t < self.min_weight:
                # Forgotten — weight too low
                pruned_timestamps.append(entry["timestamp"])
            else:
                survivors.append(entry)

        # Apply deletions
        if pruned_timestamps:
            self.l1.remove_by_timestamps(pruned_timestamps)

        deleted = initial_count - len(survivors)
        self._log.append(
            f"L1: Đã cắt {deleted}/{initial_count} mạch thần kinh. "
            f"Còn lại: {len(survivors)} bản ghi."
        )
        return {"deleted": deleted, "remaining": len(survivors)}

    # ─── L2 Pruning (Long-term) ───────────────────────────────

    def prune_l2(self) -> Dict[str, int]:
        """Prune L2 Semantic Memory by removing fully decayed entries."""
        if not self.l2:
            return {"deleted": 0, "remaining": 0}

        self._log.append("--- CẮT TỈA L2 (Vector Decay Pruning) ---")
        deleted = self.l2.prune_decayed(threshold=1.0)
        remaining = self.l2.count
        self._log.append(
            f"L2: Đã xoá {deleted} vector suy giảm. Còn lại: {remaining}."
        )
        return {"deleted": deleted, "remaining": remaining}

    # ─── Full Cycle ───────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """
        Full pruning cycle — L1 + L2.
        Call this during each "sleep" cycle.
        """
        self._log.append("====== GARBAGE COLLECTOR: FULL CYCLE ======")
        l1_result = self.prune_l1()
        l2_result = self.prune_l2()
        self._log.append("====== CYCLE COMPLETE ======")

        return {
            "l1": l1_result,
            "l2": l2_result,
            "total_deleted": l1_result["deleted"] + l2_result["deleted"],
        }

    def __repr__(self) -> str:
        return f"GarbageCollector(l1={self.l1.count}, l2={self.l2.count if self.l2 else 'N/A'})"
