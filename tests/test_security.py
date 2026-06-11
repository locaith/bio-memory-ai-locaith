"""
Security hardening tests: persona encryption keys, API auth, input bounds,
and rate limiting.
"""

import base64
import importlib
import json
import time
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi.testclient import TestClient

from bio_agent_os.core.persona import Persona
from bio_agent_os.core.secret_store import (
    LEGACY_DEFAULT_SECRET,
    LEGACY_SALT,
    SECRET_FILE_NAME,
)
from bio_agent_os.core.sqlite_store import SQLiteStore

STORAGE = "test_data"


def _legacy_cipher() -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=LEGACY_SALT,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(LEGACY_DEFAULT_SECRET.encode("utf-8")))
    return Fernet(key)


def _insert_rule_row(store: SQLiteStore, table: str, rule_id: str, encrypted_text: str):
    now = time.time()
    store.execute(
        f"""
        INSERT OR REPLACE INTO {table} (
            rule_id, encrypted_text, layer, scope, source, confidence,
            support_count, contradiction_count, state, created_at, updated_at,
            last_validated_at, valid_from, valid_to, evidence_episode_ids_json,
            superseded_by, expires_at, challenge_reasons_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule_id, encrypted_text, "project", "project", "legacy", 0.8,
            2, 0, "stable", now, now, now, now, None, "[]", None, None, "[]",
        ),
    )


# ---------------------------------------------------------------------------
# Persona encryption
# ---------------------------------------------------------------------------

def test_persona_generates_per_install_key_and_roundtrips(monkeypatch):
    monkeypatch.delenv("BIO_AGENT_SECRET_KEY", raising=False)
    persona = Persona(name="sec-test", storage_dir=STORAGE)
    rule_id = persona.add_rule("Never push directly to main.", scope="project")

    secret_path = Path(STORAGE) / SECRET_FILE_NAME
    assert secret_path.exists()
    material = json.loads(secret_path.read_text(encoding="utf-8"))
    assert material.get("secret")
    assert material.get("salt")

    reloaded = Persona(name="sec-test", storage_dir=STORAGE)
    assert reloaded.get_rules()[rule_id] == "Never push directly to main."

    row = SQLiteStore(storage_dir=STORAGE).fetchone(
        "SELECT encrypted_text FROM sec_test_persona_rules WHERE rule_id = ?",
        (rule_id,),
    )
    assert "Never push" not in row["encrypted_text"]


def test_persona_migrates_rules_encrypted_with_legacy_default_key(monkeypatch):
    monkeypatch.delenv("BIO_AGENT_SECRET_KEY", raising=False)
    # Create the table (and per-install key) first.
    Persona(name="sec-legacy", storage_dir=STORAGE)

    legacy = _legacy_cipher()
    rule_id = f"rule_{uuid.uuid4().hex}"
    legacy_token = legacy.encrypt(b"Always run migrations in a transaction.").decode("utf-8")
    store = SQLiteStore(storage_dir=STORAGE)
    _insert_rule_row(store, "sec_legacy_persona_rules", rule_id, legacy_token)

    persona = Persona(name="sec-legacy", storage_dir=STORAGE)
    assert persona.get_rules()[rule_id] == "Always run migrations in a transaction."

    # The row must have been re-encrypted with the per-install key.
    row = store.fetchone(
        "SELECT encrypted_text FROM sec_legacy_persona_rules WHERE rule_id = ?",
        (rule_id,),
    )
    with pytest.raises(InvalidToken):
        legacy.decrypt(row["encrypted_text"].encode("utf-8"))


def test_persona_skips_undecryptable_rules_without_leaking_or_deleting(monkeypatch):
    monkeypatch.delenv("BIO_AGENT_SECRET_KEY", raising=False)
    persona = Persona(name="sec-corrupt", storage_dir=STORAGE)
    good_rule_id = persona.add_rule("Review every schema change.", scope="project")

    garbage_token = "not-a-valid-fernet-token"
    bad_rule_id = f"rule_{uuid.uuid4().hex}"
    store = SQLiteStore(storage_dir=STORAGE)
    _insert_rule_row(store, "sec_corrupt_persona_rules", bad_rule_id, garbage_token)

    reloaded = Persona(name="sec-corrupt", storage_dir=STORAGE)
    rules = reloaded.get_rules()
    assert good_rule_id in rules
    assert bad_rule_id not in rules
    assert garbage_token not in reloaded.get_identity_prompt()

    # A save must not destroy the row that failed decryption.
    reloaded.add_rule("Another rule to force a save.", scope="project")
    row = store.fetchone(
        "SELECT encrypted_text FROM sec_corrupt_persona_rules WHERE rule_id = ?",
        (bad_rule_id,),
    )
    assert row is not None
    assert row["encrypted_text"] == garbage_token


def test_persona_respects_env_secret_key(monkeypatch):
    monkeypatch.setenv("BIO_AGENT_SECRET_KEY", "env-managed-secret")
    persona = Persona(name="sec-env", storage_dir=STORAGE)
    rule_id = persona.add_rule("Env-keyed rule.", scope="project")
    reloaded = Persona(name="sec-env", storage_dir=STORAGE)
    assert reloaded.get_rules()[rule_id] == "Env-keyed rule."


# ---------------------------------------------------------------------------
# API auth, validation, and rate limiting
# ---------------------------------------------------------------------------

def _build_client(monkeypatch, **env) -> TestClient:
    monkeypatch.setenv("AGENT_NAME", "api-test")
    monkeypatch.setenv("STORAGE_DIR", STORAGE)
    monkeypatch.delenv("BIO_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("BIO_AGENT_RATE_LIMIT", raising=False)
    monkeypatch.delenv("BIO_AGENT_CORS_ORIGINS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import bio_agent_os.api.main as main_module

    main_module = importlib.reload(main_module)
    return TestClient(main_module.app)


def test_api_requires_bearer_key_when_configured(monkeypatch):
    with _build_client(monkeypatch, BIO_AGENT_API_KEY="secret-key-123") as client:
        assert client.get("/api/health").status_code == 200  # public endpoint

        assert client.get("/api/state").status_code == 401
        assert (
            client.get("/api/state", headers={"Authorization": "Bearer wrong-key"}).status_code
            == 401
        )
        assert (
            client.post("/api/sleep", headers={"Authorization": "Basic abc"}).status_code
            == 401
        )

        ok = client.get("/api/state", headers={"Authorization": "Bearer secret-key-123"})
        assert ok.status_code == 200


def test_api_open_on_loopback_when_no_key_configured(monkeypatch):
    with _build_client(monkeypatch) as client:
        assert client.get("/api/status").status_code == 200


def test_api_rejects_out_of_bounds_inputs(monkeypatch):
    with _build_client(monkeypatch) as client:
        assert client.post("/api/retrieve", json={"query": "x", "top_k": 9999}).status_code == 422
        assert client.post("/api/retrieve", json={"query": "   "}).status_code == 422
        assert client.post("/api/chat", json={"message": "   "}).status_code == 422
        assert client.post("/api/ingest", json={"text": "hello", "chunk_size": 5}).status_code == 422
        assert client.get("/api/episodes", params={"limit": 100000}).status_code == 422
        assert client.post("/api/revalidation/resolve", json={"fact_kind": ""}).status_code == 422


def test_api_rate_limit_returns_429(monkeypatch):
    with _build_client(monkeypatch, BIO_AGENT_RATE_LIMIT="3") as client:
        for _ in range(3):
            assert client.get("/api/status").status_code == 200
        blocked = client.get("/api/status")
        assert blocked.status_code == 429
        assert "retry-after" in {key.lower() for key in blocked.headers.keys()}
        # Health stays reachable for monitoring even when throttled.
        assert client.get("/api/health").status_code == 200


# ---------------------------------------------------------------------------
# Multi-tenancy
# ---------------------------------------------------------------------------

ADMIN = {"Authorization": "Bearer admin-key-123"}
TENANT_A = {"Authorization": "Bearer tenant-a-key"}
TENANT_MULTI = {"Authorization": "Bearer tenant-multi-key"}

TENANT_ENV = {
    "BIO_AGENT_API_KEY": "admin-key-123",
    "BIO_AGENT_TENANT_KEYS": "tenant-a-key=ws-a,tenant-multi-key=ws-a|ws-b",
}


def test_tenant_key_authenticates_but_cannot_reach_control_plane(monkeypatch):
    with _build_client(monkeypatch, **TENANT_ENV) as client:
        assert client.get("/api/status", headers=TENANT_A).status_code == 200
        for path in ("/api/state", "/api/graph", "/api/audit", "/api/approvals"):
            assert client.get(path, headers=TENANT_A).status_code == 403
        assert client.post("/api/sleep", headers=TENANT_A).status_code == 403
        assert client.post("/api/reset", headers=TENANT_A).status_code == 403
        # The admin key still has full access.
        assert client.get("/api/state", headers=ADMIN).status_code == 200


def test_tenant_workspace_enforcement(monkeypatch):
    with _build_client(monkeypatch, **TENANT_ENV) as client:
        # Foreign workspace is rejected.
        denied = client.post(
            "/api/retrieve",
            json={"query": "deploy policy", "workspace_id": "ws-b"},
            headers=TENANT_A,
        )
        assert denied.status_code == 403

        # Omitted workspace defaults to the tenant's only workspace.
        ok = client.post("/api/retrieve", json={"query": "deploy policy"}, headers=TENANT_A)
        assert ok.status_code == 200
        assert ok.json()["retrieval_state"]["workspace_id"] == "ws-a"
        assert ok.json()["retrieval_state"]["allowed_workspaces"] == ["ws-a"]

        # A multi-workspace tenant must pick a workspace explicitly.
        ambiguous = client.post(
            "/api/retrieve", json={"query": "deploy policy"}, headers=TENANT_MULTI
        )
        assert ambiguous.status_code == 403
        explicit = client.post(
            "/api/retrieve",
            json={"query": "deploy policy", "workspace_id": "ws-b"},
            headers=TENANT_MULTI,
        )
        assert explicit.status_code == 200

        # Unfiltered exact-memory listing is admin-only; scoped recall works.
        assert client.get("/api/exact-memories", headers=TENANT_A).status_code == 403
        assert (
            client.get("/api/exact-memories", params={"query": "code"}, headers=TENANT_A).status_code
            == 200
        )


def test_l2_results_are_isolated_per_tenant(monkeypatch):
    with _build_client(monkeypatch, **TENANT_ENV) as client:
        import bio_agent_os.api.main as main_module

        main_module.l2.store(
            content="Tenant A deploys with blue-green strategy.",
            importance=8.0,
            workspace_id="ws-a",
        )
        main_module.l2.store(
            content="Tenant B secret rollout password is hunter2.",
            importance=8.0,
            workspace_id="ws-b",
        )
        main_module.l2.store(
            content="Global memory without workspace tag.",
            importance=8.0,
        )

        response = client.post(
            "/api/retrieve",
            json={"query": "deploys rollout strategy password", "top_k": 10},
            headers=TENANT_A,
        )
        assert response.status_code == 200
        contents = [item["content"] for item in response.json()["l2_results"]]
        assert any("blue-green" in content for content in contents)
        assert all("hunter2" not in content for content in contents)
        assert all("Global memory" not in content for content in contents)

        # The admin key sees everything.
        admin_response = client.post(
            "/api/retrieve",
            json={"query": "deploys rollout strategy password", "top_k": 10},
            headers=ADMIN,
        )
        admin_contents = [item["content"] for item in admin_response.json()["l2_results"]]
        assert any("hunter2" in content for content in admin_contents)
