"""DEPLOYED CONFIG AUTHORITY — file settings mới là bên chọn mode, không phải
harness đang chạy thí nghiệm.

Nợ bằng chứng do CI-CLOSEOUT khai: nửa `env → mode` đã có regression test
(`test_shadow_mode.py`), nhưng nửa `settings.json → env → runtime` chỉ có MỘT
nhân chứng deployment (`activation/A5v2_1_deployed_config_witness.py`).

    HARNESS-FORCED ENV != DEPLOYED CONFIG AUTHORITY

Chứng minh hook path chạy đúng KHI BỊ ÉP mode là chứng minh một câu khác với
câu ta cần: rằng cấu hình đã deploy là bên thật sự quyết định.

Ranh giới trung thực của test này: nó khoá được chuỗi
`settings.json → env của tiến trình con → mode hiệu lực → hành vi ghi`.
Nó KHÔNG khoá được việc Claude Code có đọc file settings hay không — đó là
phần mềm bên ngoài repo này, và chỗ đó vẫn chỉ có nhân chứng deployment.
Nói rõ ra thay vì để chữ VERIFIED phủ lên cả đoạn không đo được.
"""
from __future__ import annotations

import json
import os
import subprocess
import sqlite3
import sys
from pathlib import Path

import pytest

HOOK = [sys.executable, "-m", "bio_agent_os.cognitive.hook_cli",
        "UserPromptSubmit"]
PAYLOAD = {"hook_event_name": "UserPromptSubmit", "session_id": "dca",
           "prompt": "khách Bình Minh chốt hợp đồng, mã DCA-AUTHORITY."}


def _settings(mode: str) -> dict:
    """Đúng hình dạng của `.claude/settings.json` đã deploy."""
    return {"env": {"BIO_AGENT_TENANT_ID": "locaith",
                    "BIO_AGENT_WORKSPACE_ID": "ws",
                    "BIO_AGENT_WORKSPACE_STRATEGY": "explicit",
                    "BIO_AGENT_PROJECTION_MODE": mode}}


def _run_hook_under(settings: dict, db: Path, cwd: Path) -> None:
    """Env đi TỪ FILE SETTINGS, không từ phiên đang chạy.

    `pop` trước khi `update` là phần quan trọng: nếu để env của phiên rò
    xuống, test sẽ đo chính niềm tin của mình chứ không đo quyền của file."""
    env = dict(os.environ)
    env.pop("BIO_AGENT_PROJECTION_MODE", None)
    env.update(settings["env"])
    env["BIO_MEMORY_DB"] = str(db)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(HOOK, input=json.dumps(PAYLOAD).encode(),
                       capture_output=True, env=env, cwd=str(cwd), timeout=180)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-500:]


def _shape(db: Path) -> tuple[int, int, int]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        ev = conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0]
        mem = conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
        try:
            jobs = conn.execute(
                "SELECT COUNT(*) FROM projection_outbox").fetchone()[0]
        except sqlite3.OperationalError:
            jobs = 0
    finally:
        conn.close()
    return ev, mem, jobs


@pytest.mark.parametrize("mode,expect_jobs", [("legacy", 0), ("outbox", 1)])
def test_the_settings_file_decides_the_write_path(tmp_path, mode, expect_jobs):
    """Đổi MỘT dòng trong settings → hành vi ghi của tiến trình con đổi theo.

    Không có chuỗi "outbox" nào được hard-code trong test: mode đi từ dict
    settings vào env, rồi ra hành vi quan sát được."""
    db = tmp_path / f"{mode}.db"
    _run_hook_under(_settings(mode), db, tmp_path)
    events, memories, jobs = _shape(db)
    assert events == 1, "event luôn phải được ghi, mọi mode"
    assert jobs == expect_jobs, (
        f"settings nói mode={mode!r} nhưng số hàng outbox là {jobs}, "
        f"đáng ra {expect_jobs}")
    assert memories == 1, "cả hai mode đều phải kết thúc bằng đúng một ký ức"


def test_session_env_must_not_override_the_settings_file(tmp_path):
    """Env của phiên đang chạy KHÔNG được thắng file settings.

    Đây là nửa mà nhân chứng A5-v2.1 sinh ra để bắt: một harness ép mode rồi
    kết luận 'deployed config đúng' là đo nhầm bên."""
    db = tmp_path / "conflict.db"
    settings = _settings("legacy")
    env = dict(os.environ)
    env["BIO_AGENT_PROJECTION_MODE"] = "outbox"      # phiên nói OUTBOX...
    env.update(settings["env"])                       # ...file nói LEGACY
    env["BIO_MEMORY_DB"] = str(db)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(HOOK, input=json.dumps(PAYLOAD).encode(),
                       capture_output=True, env=env, cwd=str(tmp_path),
                       timeout=180)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-500:]
    _, _, jobs = _shape(db)
    assert jobs == 0, (
        "env của phiên thắng file settings — file không còn là bên quyết định")


def test_mutant_hard_coded_mode_breaks_settings_authority(tmp_path):
    """M: hard-code mode trong đường hook → settings mất quyền.

    Nếu test đổi-settings ở trên vẫn xanh dưới mutant này thì nó chưa đo
    quyền của file, nó chỉ đang quan sát một giá trị mặc định trùng khớp.

    Mutant chạy bằng một WRAPPER tường minh chứ không bằng `sitecustomize`:
    sitecustomize được `site` nạp trước khi editable-install kịp vào sys.path,
    nên bản vá im lặng không chạy và mutant "chết" vì NHẮM TRƯỢT — một
    false-green của chính mutant, đúng họ lỗi instrument mà closeout vừa ghi.
    """
    wrapper = tmp_path / "mutant_launcher.py"
    wrapper.write_text("\n".join([
        "from bio_agent_os.cognitive import facade, shadow",
        # vá vào tên ĐÃ BIND trong facade, không phải vào module gốc
        "facade.current_mode = lambda *a, **k: shadow.ProjectionMode.OUTBOX",
        "from bio_agent_os.cognitive.hook_cli import main",
        "main()",
    ]), encoding="utf-8")

    db = tmp_path / "mutant.db"
    env = dict(os.environ)
    env.pop("BIO_AGENT_PROJECTION_MODE", None)
    env.update(_settings("legacy")["env"])            # file nói LEGACY
    env["BIO_MEMORY_DB"] = str(db)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(wrapper), "UserPromptSubmit"],
                       input=json.dumps(PAYLOAD).encode(),
                       capture_output=True, env=env, cwd=str(tmp_path),
                       timeout=180)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-500:]
    _, _, jobs = _shape(db)
    assert jobs == 1, (
        "mutant hard-code OUTBOX mà hành vi vẫn theo settings=legacy — "
        "test đổi-settings ở trên chưa chạm được vào đường quyết định mode")
