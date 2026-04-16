"""
core/persona.py — Core Identity Manager (Bản sắc bất biến).

Quản lý "Hộp Đen Cốt Lõi" — tập hợp các quy tắc logic trừu tượng
đã được đúc kết từ hàng ngàn sự kiện, không bao giờ bị xoá.
Đây chính là "Linh hồn" của AI.
"""

import os
import json
import time
from typing import Dict, Optional


class Persona:
    """
    Core Identity — Permanent logic rules that define who the AI *is*.
    
    These rules survive all pruning cycles and represent the AI's
    accumulated wisdom, distilled from raw experiences.
    
    Usage:
        persona = Persona(name="My-Agent", storage_dir="data/")
        persona.add_rule("Khi giao tiếp, luôn đi thẳng vào trọng tâm.")
        print(persona.get_identity_prompt())  # For LLM system prompt
    """

    def __init__(self, name: str = "Bio-AI", storage_dir: str = "data"):
        self.name = name
        self.storage_dir = storage_dir
        self._rules: Dict[str, str] = {}
        self._filepath = os.path.join(storage_dir, f"{name}_core_identity.json")
        self.load()

    # ─── Rule Management ──────────────────────────────────────

    def add_rule(self, rule_text: str) -> str:
        """Add a new permanent logic rule. Returns the rule ID."""
        rule_id = f"rule_{int(time.time() * 1000)}"
        self._rules[rule_id] = rule_text
        self.save()
        return rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID (rare — only for manual correction)."""
        if rule_id in self._rules:
            del self._rules[rule_id]
            self.save()
            return True
        return False

    def get_rules(self) -> Dict[str, str]:
        """Get all core logic rules."""
        return dict(self._rules)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    # ─── Identity Prompt ──────────────────────────────────────

    def get_identity_prompt(self) -> str:
        """
        Generate a system prompt that embeds Core Identity into LLM context.
        This is injected at the beginning of every LLM call.
        """
        if not self._rules:
            return f"Bạn là {self.name}. Chưa có kiến thức cốt lõi nào được tích luỹ."

        rules_text = "\n".join(
            f"  {i+1}. {rule}" for i, rule in enumerate(self._rules.values())
        )
        return f"""Bạn là {self.name}. Dưới đây là Kiến thức Cốt lõi Vĩnh viễn (Core Identity) — 
những quy tắc logic trừu tượng đã được đúc kết từ hàng trăm sự kiện thực tế.
Hãy luôn vận dụng các quy tắc này khi phân tích và trả lời:

{rules_text}"""

    # ─── Persistence ──────────────────────────────────────────

    def save(self):
        """Persist core identity to disk."""
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump({
                "name": self.name,
                "rules": self._rules,
                "updated_at": time.time(),
            }, f, ensure_ascii=False, indent=2)

    def load(self):
        """Load core identity from disk."""
        if os.path.exists(self._filepath):
            with open(self._filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._rules = data.get("rules", {})

    def __repr__(self) -> str:
        return f"Persona(name='{self.name}', rules={self.rule_count})"
