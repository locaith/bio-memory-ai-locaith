"""
core/persona.py — Core Identity Manager (Bản sắc bất biến).

Quản lý "Hộp Đen Cốt Lõi" — tập hợp các quy tắc logic trừu tượng.
Phiên bản 0.3.0 bổ sung Hệ thống Mã hóa (Encryption) bảo vệ
bí mật doanh nghiệp và chuẩn ERP.
"""

import os
import json
import time
import base64
from typing import Dict, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

class Persona:
    """
    Core Identity with Enterprise-grade AES encryption.
    """

    def __init__(self, name: str = "Bio-AI", storage_dir: str = "data"):
        self.name = name
        self.storage_dir = storage_dir
        self._rules: Dict[str, str] = {}
        self._filepath = os.path.join(storage_dir, f"{name}_core_identity.json")
        
        # Setup Encryption Keys
        self._crypto_key = os.getenv("BIO_AGENT_SECRET_KEY", "default-insecure-key-change-in-prod-erp")
        self._fernet = self._setup_cipher()
        
        self.load()

    def _setup_cipher(self) -> Fernet:
        # Generate stable fernet key from password
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"locaith-bio-memory",
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self._crypto_key.encode()))
        return Fernet(key)

    def _encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode('utf-8')).decode('utf-8')
        
    def _decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode('utf-8')).decode('utf-8')
        except Exception:
            # Fallback for plain text if older version
            return token

    def add_rule(self, rule_text: str) -> str:
        rule_id = f"rule_{int(time.time() * 1000)}"
        self._rules[rule_id] = rule_text
        self.save()
        return rule_id

    def get_rules(self) -> Dict[str, str]:
        return dict(self._rules)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def get_identity_prompt(self) -> str:
        if not self._rules:
            return f"Bạn là {self.name}. Chưa có kiến thức cốt lõi nào được tích luỹ."

        rules_text = "\n".join(
            f"  {i+1}. {rule}" for i, rule in enumerate(self._rules.values())
        )
        return f"""Bạn là {self.name}. Dưới đây là Kiến thức Cốt lõi Vĩnh viễn (Core Identity) — 
những quy tắc logic trừu tượng đã được đúc kết từ hàng trăm sự kiện thực tế.
Hãy luôn vận dụng các quy tắc này khi phân tích và trả lời:

{rules_text}"""

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        # Encrypt the rules directly in JSON
        encrypted_rules = {k: self._encrypt(v) for k, v in self._rules.items()}
        
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump({
                "name": self.name,
                "rules": encrypted_rules,
                "updated_at": time.time(),
                "encrypted": True
            }, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            with open(self._filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                is_encrypted = data.get("encrypted", False)
                raw_rules = data.get("rules", {})
                
                if is_encrypted:
                    self._rules = {k: self._decrypt(v) for k, v in raw_rules.items()}
                else:
                    self._rules = raw_rules

    def __repr__(self) -> str:
        return f"Persona(name='{self.name}', rules={self.rule_count}, encrypted=True)"
