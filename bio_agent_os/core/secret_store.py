"""
Per-install secret material for Bio-Agent OS encryption.

Resolution order for the encryption secret:
1. ``BIO_AGENT_SECRET_KEY`` environment variable.
2. A per-install secret file inside the storage directory, generated on
   first run with a cryptographically random value.

The KDF salt is always persisted in the secret file so key derivation is
unique per install but stable across restarts. Earlier releases shipped a
hardcoded default key and a fixed salt; those values are kept here only as
*decrypt-time* fallbacks so existing databases migrate transparently.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Optional, Tuple

logger = logging.getLogger("bio_agent_os.secret_store")

SECRET_FILE_NAME = ".bio_agent_secret.json"

# Legacy values from <= v0.6.1. Never used to encrypt new data.
LEGACY_DEFAULT_SECRET = "default-insecure-key-change-in-prod-erp"
LEGACY_SALT = b"locaith-bio-memory"


def _secret_file_path(storage_dir: str) -> str:
    return os.path.join(storage_dir, SECRET_FILE_NAME)


def _read_secret_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_secret_file(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def resolve_secret_material(storage_dir: str) -> Tuple[str, bytes]:
    """
    Return ``(secret, salt)`` for this installation, generating and
    persisting any missing material on first use.
    """
    os.makedirs(storage_dir, exist_ok=True)
    path = _secret_file_path(storage_dir)
    data = _read_secret_file(path)
    dirty = False

    salt_hex: Optional[str] = data.get("salt")
    if not isinstance(salt_hex, str) or len(salt_hex) < 16:
        salt_hex = secrets.token_hex(16)
        data["salt"] = salt_hex
        dirty = True

    env_secret = (os.getenv("BIO_AGENT_SECRET_KEY") or "").strip()
    if env_secret:
        secret = env_secret
    else:
        stored_secret = data.get("secret")
        if isinstance(stored_secret, str) and stored_secret:
            secret = stored_secret
        else:
            secret = secrets.token_urlsafe(32)
            data["secret"] = secret
            dirty = True
            logger.info(
                "Generated a new per-install encryption secret at %s. "
                "Set BIO_AGENT_SECRET_KEY to manage the key yourself.",
                path,
            )

    if dirty:
        _write_secret_file(path, data)

    return secret, bytes.fromhex(salt_hex)
