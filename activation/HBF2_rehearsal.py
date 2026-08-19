"""HBF-2 — OFFLINE HISTORICAL NATURALIZATION REHEARSAL. Store thật CHỈ ĐỌC.

    REAL STORE MIGRATION: NOT PERFORMED. Không nhánh nào mở write
    connection vào memory.db thật. Đường duy nhất về canonical là
    install_generation — và ở đây nó chỉ được trỏ vào DISPOSABLE canonical.

Diễn tập cháy nổ cho cả sở di trú trước khi mở cửa thật (HBF-3, chưa ký):
  snapshot tươi → candidate offline → classify tươi (structured closure)
  → adopt một transaction → abort-admissibility → K1–K4 → M1/M2
  → install rehearsal vào disposable canonical → fresh-process verify.

Usage:  python activation/HBF2_rehearsal.py
        python activation/HBF2_rehearsal.py --verify-installed <path>
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):        # console Windows mặc định cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bio_agent_os.cognitive.historical_adoption import (          # noqa: E402
    ADOPT_CLASSES, AdmissibilityError, InjectedAbort, MIGRATION_ACTOR,
    adopt, adoption_gate, adoption_invariants, classify_store, tables_digest)
from bio_agent_os.cognitive.projection_engine import (            # noqa: E402
    ProjectionReplayEngine)
from bio_agent_os.cognitive.store_generation import (             # noqa: E402
    certify_candidate, install_generation)
from bio_agent_os.cognitive.tombstones import buried_among        # noqa: E402

REAL = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
            r"\.bio-agent-os\memory.db")
WORK = Path(__file__).parent / "HBF2"
SNAP = WORK / "hbf2_frozen_source.db"
CANDIDATE = WORK / "hbf2_candidate.db"
DISPOSABLE = WORK / "hbf2_disposable_canonical.db"
QUARANTINE = WORK / "quarantine"
PLAN_1_1 = Path(__file__).parent / "HBF0" / "hbf1_1_plan.json"

report: dict = {"phase": "HBF-2", "verdict": None,
                "real_store_migration": "NOT PERFORMED"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy(src: Path, dest: Path) -> Path:
    """Copy qua sqlite backup API — không copy2 file đang mở (bài học A5)."""
    if dest.exists():
        dest.unlink()
    for side in (".db-wal", ".db-shm"):
        p = dest.with_suffix(side)
        if p.exists():
            p.unlink()
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(dest))
    s.backup(d)
    d.close()
    s.close()
    return dest


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _slot_fn():
    """Slot extractor tất định — mở trên THROWAWAY copy, không phải candidate."""
    from bio_agent_os.cognitive.facade import MemoryOS
    throwaway = _copy(SNAP, WORK / "hbf2_slotcheck.db")
    mos = MemoryOS(throwaway, projection_mode="outbox")
    return mos, (lambda content, source: mos._structured_slot(content, source))


def _class_counts(rep) -> dict:
    return {k: v for k, v in rep.counts.items() if v}


def verify_installed(path: str) -> None:
    """Fresh process: mở disposable canonical ĐÃ INSTALL, chỉ đọc, in JSON."""
    p = Path(path)
    out: dict = {"file_sha256_before_open": _sha(p)}
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out["integrity"] = [r[0] for r in conn.execute("PRAGMA integrity_check")][:1]
    out["fk_violations"] = len(list(conn.execute("PRAGMA foreign_key_check")))
    inv = adoption_invariants(conn)
    out["invariants"] = inv
    out["audit_rows"] = conn.execute(
        "SELECT management_action, COUNT(*) FROM projection_adoption_audit "
        "GROUP BY management_action").fetchall()
    out["audit_rows"] = {r[0]: r[1] for r in out["audit_rows"]}
    # replay debt semantics — scan() chỉ đọc; candidate còn lại phải toàn
    # tombstoned (bia mộ là authority, được tách khỏi actionable debt)
    scan = ProjectionReplayEngine(conn).scan()
    cand_events = {c.event_id for c in scan.candidates}
    tombs = buried_among(conn, cand_events)
    out["replay_scan_candidates"] = len(scan.candidates)
    out["replay_candidates_tombstoned"] = len(cand_events & tombs)
    out["actionable_replay_debt"] = len(cand_events - tombs)
    conn.close()
    # Cùng luật với certify_candidate: chỉ -wal CÓ NỘI DUNG là bằng chứng
    # chưa checkpoint. -shm luôn 32KB kể cả cho reader mode=ro — không phải
    # stale. (Chính check này từng bắt nhầm -shm — probe 19/08.)
    wal = Path(str(p) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        out["stale_sidecars"] = [str(wal)]
    print(json.dumps(out, ensure_ascii=False, default=str))


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # ---- 1. FREEZE: snapshot tươi, certify, đóng băng population
    _copy(REAL, SNAP)
    snap_sha = certify_candidate(SNAP)
    report["source_snapshot_sha256"] = snap_sha
    print(f"[1] frozen source certified  {snap_sha[:16]}")

    # ---- 2. CANDIDATE offline
    _copy(SNAP, CANDIDATE)
    conn = _open(CANDIDATE)

    # ---- 3. PRE-ADOPTION: classify tươi + structured closure TRƯỚC transaction
    mos_slot, slot_fn = _slot_fn()
    try:
        rep = classify_store(conn, slot_fn=slot_fn)
    finally:
        mos_slot.close()
    counts = rep.counts
    report["fresh_population"] = {"total_events": rep.total_events,
                                  "counts": _class_counts(rep)}
    report["drift_stats"] = rep.drift_stats
    closure: dict[str, int] = {}
    proofs: dict[str, int] = {}
    for r in rep.rows:
        if r.cls in ADOPT_CLASSES:
            closure[r.structured_content_status] = \
                closure.get(r.structured_content_status, 0) + 1
            proofs[r.proof] = proofs.get(r.proof, 0) + 1
    report["structured_content_closure"] = closure
    report["proof_class_counts"] = proofs
    print(f"[3] fresh classify: {_class_counts(rep)}")
    print(f"    closure: {closure}")
    print(f"    proofs : {proofs}")

    # đối chiếu HBF-1.1 (quan sát, không ép số — số đo mới thắng)
    if PLAN_1_1.exists():
        old = json.loads(PLAN_1_1.read_text(encoding="utf-8"))
        drift_vs_old = {k: counts.get(k, 0) - old["counts"].get(k, 0)
                        for k in set(old["counts"]) | set(counts)
                        if counts.get(k, 0) != old["counts"].get(k, 0)}
        report["delta_vs_hbf1_1"] = drift_vs_old
        print(f"    delta vs HBF-1.1: {drift_vs_old or 'không'}")

    adoption_gate(rep)                    # cổng admissibility
    print("[3] adoption gate: OPEN — lớp chặn = 0, structured đã đóng")

    # ---- 4. ABORT ADMISSIBILITY trên copy riêng (trước lễ thật)
    abort_db = _copy(CANDIDATE, WORK / "hbf2_abort_probe.db")
    ac = _open(abort_db)
    a_before = tables_digest(ac)
    try:
        adopt(ac, rep, migration_run_id="hbf2-abort-probe",
              source_snapshot_sha256=snap_sha, fail_before_commit=True)
        raise AssertionError("InjectedAbort không nổ")
    except InjectedAbort:
        pass
    abort_ok = (tables_digest(ac) == a_before
                and adoption_invariants(ac)["migration_rows_total"] == 0
                and ac.execute("SELECT COUNT(*) FROM projection_adoption_audit"
                               ).fetchone()[0] == 0)
    ac.close()
    report["atomic_abort"] = "ZERO PARTIAL STATE" if abort_ok else "FAIL"
    assert abort_ok
    print("[4] atomic abort: zero partial state")

    # ---- 5. LỄ NHẬP TỊCH trên candidate — MỘT transaction
    run_id = f"hbf2-{int(t0)}"
    sem_before = tables_digest(conn)
    result = adopt(conn, rep, migration_run_id=run_id,
                   source_snapshot_sha256=snap_sha)
    sem_after = tables_digest(conn)
    report["migration_transaction"] = {
        "migration_run_id": run_id,
        "adopted": result.adopted,
        "event_only_skipped": result.skipped_event_only,
        "tombstone_excluded": result.excluded_tombstoned,
        "managed_noops": counts["ALREADY_MANAGED"],
        "live_queue_untouched": counts["LIVE_QUEUE"],
        "outbox_inserted": result.outbox_inserted,
        "ledger_inserted": result.ledger_inserted,
        "audit_inserted": result.audit_inserted,
    }
    report["memory_semantic_delta"] = \
        "0 (digest bằng)" if sem_before == sem_after else "NONZERO — FAIL"
    assert sem_before == sem_after
    inv = adoption_invariants(conn)
    assert all(v == 0 for k, v in inv.items() if k != "migration_rows_total")
    replay = ProjectionReplayEngine(conn).replay(dry_run=False)
    report["post_adopt_replay"] = {
        "enqueued": replay.enqueued, "reset": replay.reset,
        "skipped_tombstoned": replay.skipped_tombstoned}
    assert replay.enqueued == 0 and replay.reset == 0
    conn.commit()
    print(f"[5] adopted={result.adopted} skipped={result.skipped_event_only} "
          f"tombstone={result.excluded_tombstoned} — semantic delta 0, "
          f"replay actionable 0, tombstoned held {replay.skipped_tombstoned}")

    # ---- 6. K1–K4 trên COPY của candidate đã adopt (data thật, forensic riêng)
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.reconciliation_worker import worker_for
    from bio_agent_os.cognitive.forgetting import forget_derived
    knives: dict[str, str] = {}

    # K1 — REPLAY không duplicate + ledger authority
    k1 = _copy(CANDIDATE, WORK / "hbf2_k1.db")
    m1 = MemoryOS(k1, projection_mode="outbox")
    c1 = m1.memories.conn
    n_before = c1.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
    ProjectionReplayEngine(c1).replay(dry_run=False)
    worker_for(m1, lease_seconds=300).run_once(batch_size=100)
    n_after = c1.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
    victim = c1.execute(
        "SELECT event_id FROM projection_ledger WHERE worker_id=? LIMIT 1",
        (MIGRATION_ACTOR,)).fetchone()[0]
    c1.execute("DELETE FROM projection_outbox WHERE event_id=?", (victim,))
    c1.commit()
    r1 = ProjectionReplayEngine(c1).replay(dry_run=False)
    worker_for(m1, lease_seconds=300).run_once(batch_size=10)
    n_final = c1.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
    m1.close()
    k1_ok = n_before == n_after == n_final and r1.enqueued == 1
    knives["K1_replay_no_duplicate"] = "PASS" if k1_ok else \
        f"FAIL {n_before}/{n_after}/{n_final} enq={r1.enqueued}"

    # K2 — FORGET không resurrection (trên copy forensic, sẽ huỷ)
    k2 = _copy(CANDIDATE, WORK / "hbf2_k2.db")
    m2 = MemoryOS(k2, projection_mode="outbox")
    c2m = m2.memories.conn
    target = rep.of("ADOPT_FULL_CONTRACT")[0].target_id
    forget_derived(m2, memory_id=target, needle="hbf2")
    ProjectionReplayEngine(c2m).replay(dry_run=False)
    worker_for(m2, lease_seconds=300).run_once(batch_size=100)
    back = c2m.execute("SELECT COUNT(*) FROM cognitive_memories "
                       "WHERE memory_id=?", (target,)).fetchone()[0]
    m2.close()
    knives["K2_forget_no_resurrection"] = "PASS" if back == 0 else \
        f"FAIL — sống lại {back}"

    # K3 — RESTART bền
    k3 = _copy(CANDIDATE, WORK / "hbf2_k3.db")
    c3 = _open(k3)
    inv3 = adoption_invariants(c3)
    audit3 = c3.execute("SELECT COUNT(*) FROM projection_adoption_audit "
                        "WHERE management_action='adopted'").fetchone()[0]
    c3.close()
    k3_ok = (all(v == 0 for k, v in inv3.items()
                 if k != "migration_rows_total")
             and audit3 == result.adopted)
    knives["K3_restart_durable"] = "PASS" if k3_ok else f"FAIL {inv3}"

    # K4 — REAPPLY idempotent: cùng report + classify tươi, cả hai phải 0
    before4 = tables_digest(conn, ("cognitive_memories", "memory_source_events",
                                   "memory_tombstones", "cognitive_events",
                                   "projection_outbox", "projection_ledger",
                                   "projection_adoption_audit"))
    again = adopt(conn, rep, migration_run_id=run_id + "-reapply",
                  source_snapshot_sha256=snap_sha)
    fresh4 = classify_store(conn)     # sau adopt: mọi hàng cũ đã managed
    third = adopt(conn, fresh4, migration_run_id=run_id + "-reapply2",
                  source_snapshot_sha256=snap_sha)
    after4 = tables_digest(conn, ("cognitive_memories", "memory_source_events",
                                  "memory_tombstones", "cognitive_events",
                                  "projection_outbox", "projection_ledger",
                                  "projection_adoption_audit"))
    k4_ok = (again.adopted == again.audit_inserted == 0
             and third.adopted == 0
             and all(fresh4.counts[k] == 0 for k in ADOPT_CLASSES)
             and before4 == after4)
    knives["K4_reapply_idempotent"] = "PASS" if k4_ok else \
        f"FAIL again={again.adopted} third={third.adopted}"
    report["knives"] = knives
    for k, v in knives.items():
        print(f"[6] {k:<28} {v}")
    assert all(v == "PASS" for v in knives.values())

    # ---- 7. M1 / M2 trên copies
    mutants: dict[str, str] = {}
    m1a = _copy(SNAP, WORK / "hbf2_m1a.db")
    cm = _open(m1a)
    cm.execute("DELETE FROM memory_source_events")
    cm.commit()
    blind = classify_store(cm)
    try:
        adoption_gate(blind)
        mutants["M1_missing_ledger_is_missing_projection"] = "SURVIVED — FAIL"
    except AdmissibilityError:
        mutants["M1_missing_ledger_is_missing_projection"] = (
            f"DIES — TRUE_MISSING={blind.counts['TRUE_MISSING']} chặn cổng")
    cm.close()

    # M1 dangerous-path witness trên data thật (copy forensic)
    m1b = _copy(CANDIDATE, WORK / "hbf2_m1b.db")
    mb = MemoryOS(m1b, projection_mode="outbox")
    cb = mb.memories.conn
    v2 = rep.of("ADOPT_FULL_CONTRACT")[1]
    cb.execute("DELETE FROM projection_ledger WHERE event_id=?", (v2.event_id,))
    cb.execute("DELETE FROM projection_outbox WHERE event_id=?", (v2.event_id,))
    cb.commit()
    caught = adoption_invariants(cb)["audit_adopted_without_ledger"]
    ProjectionReplayEngine(cb).replay(dry_run=False)
    worker_for(mb, lease_seconds=300).run_once(batch_size=10)
    dup = cb.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content="
        "(SELECT content FROM cognitive_memories WHERE memory_id=?)",
        (v2.target_id,)).fetchone()[0]
    mb.close()
    report["m1_dangerous_path_witness"] = (
        f"invariant bắt tại cửa sổ phát hiện (audit_without_ledger={caught}); "
        f"phớt lờ nó → duplicate={dup} trên data thật")
    mutants["M1_witness"] = "BLEEDS AS PREDICTED" if (caught == 1 and dup == 2) \
        else f"UNEXPECTED caught={caught} dup={dup}"

    # M2 — đổi MỘT trường FULL thật sự kiểm, content giữ nguyên
    m2p = _copy(SNAP, WORK / "hbf2_m2.db")
    cm2 = _open(m2p)
    full_target = rep.of("ADOPT_FULL_CONTRACT")[0]
    cm2.execute("UPDATE cognitive_memories SET trust_tier=4 WHERE memory_id=?",
                (full_target.target_id,))
    cm2.commit()
    mos_s2, slot2 = _slot_fn()
    try:
        rep_m2 = classify_store(cm2, slot_fn=slot2)
    finally:
        mos_s2.close()
    bad = [r for r in rep_m2.of("DIVERGENT")
           if r.event_id == full_target.event_id]
    try:
        adoption_gate(rep_m2)
        mutants["M2_full_field_altered"] = "SURVIVED — FAIL"
    except AdmissibilityError:
        witness = bad[0].reasons if bad else []
        mutants["M2_full_field_altered"] = (
            f"DIES — DIVERGENT chặn cổng; witness={witness[:1]}")
    cm2.close()
    report["mutants"] = mutants
    for k, v in mutants.items():
        print(f"[7] {k:<40} {v}")
    assert "FAIL" not in json.dumps(mutants)

    # ---- 8. INSTALL REHEARSAL — disposable canonical, KHÔNG BAO GIỜ là REAL
    conn.close()
    _copy(SNAP, DISPOSABLE)               # disposable đóng vai store thật
    assert DISPOSABLE.resolve() != REAL.resolve()
    manifest = install_generation(DISPOSABLE, CANDIDATE, QUARANTINE)
    report["install_rehearsal"] = {
        "target": "DISPOSABLE (không phải real DB)",
        "manifest": {k: str(v)[:64] for k, v in manifest.items()},
    }
    print(f"[8] install_generation → disposable: "
          f"{manifest.get('status', manifest)}")

    verify = subprocess.run(
        [sys.executable, __file__, "--verify-installed", str(DISPOSABLE)],
        capture_output=True, text=True, timeout=300)
    assert verify.returncode == 0, verify.stderr[-2000:]
    fresh_eyes = json.loads(verify.stdout.strip().splitlines()[-1])
    report["fresh_process_verify"] = fresh_eyes
    assert fresh_eyes["file_sha256_before_open"] == \
        manifest["installed_sha256"], "danh tính installed lệch manifest"
    assert fresh_eyes["integrity"] == ["ok"]
    assert fresh_eyes["fk_violations"] == 0
    assert fresh_eyes["actionable_replay_debt"] == 0
    assert "stale_sidecars" not in fresh_eyes
    inv_f = fresh_eyes["invariants"]
    assert all(v == 0 for k, v in inv_f.items() if k != "migration_rows_total")
    print(f"[8] fresh process: integrity ok, fk 0, actionable debt 0, "
          f"tombstoned held {fresh_eyes['replay_candidates_tombstoned']}, "
          f"audit={fresh_eyes['audit_rows']}")

    report["verdict"] = "HBF-2 OFFLINE REHEARSAL VERIFIED"
    report["elapsed_s"] = round(time.time() - t0, 1)
    (WORK / "hbf2_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\nHBF-2 OFFLINE REHEARSAL VERIFIED — {report['elapsed_s']}s. "
          f"REAL STORE MIGRATION: NOT PERFORMED.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify-installed":
        verify_installed(sys.argv[2])
    else:
        main()
