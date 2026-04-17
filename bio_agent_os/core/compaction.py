"""
Deterministic memory compaction utilities.
"""

from typing import Dict


class MemoryCompactor:
    def __init__(self, max_chars: int = 1800):
        self.max_chars = max_chars

    def compact(self, text: str) -> Dict[str, object]:
        original_length = len(text)
        if original_length <= self.max_chars:
            return {
                "content": text,
                "was_compacted": False,
                "original_length": original_length,
                "compacted_length": original_length,
            }

        head = max(300, self.max_chars // 3)
        tail = max(300, self.max_chars // 3)
        compacted = (
            text[:head]
            + "\n...[COMPACTED FOR MEMORY STABILITY]...\n"
            + text[-tail:]
        )
        return {
            "content": compacted,
            "was_compacted": True,
            "original_length": original_length,
            "compacted_length": len(compacted),
        }
