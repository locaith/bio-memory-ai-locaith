"""Migrate a real store to `aspect_resolver@2`, on a clone first.

The subject stored on a memory row was produced by a resolver that could run a
name across a clause boundary, so "…thông tin trước là sai, Bùi Cường…" was
filed under the subject "sai Bùi Cường". `SUBJECT_IDENTITY_READ` reads rows the
old way when their `resolver_version` is stale, so an unmigrated store is safe —
but identity separation is not yet in force for those rows either.

Nothing here decides anything on the operator's behalf. In particular a row
whose subject cannot be resolved stays unresolved: **a migration that resolves
ambiguity is not a migration, it is an opinion.**

    A  snapshot the real store, with -wal/-shm, into an immutable directory
    B  hash, row counts, runtime fingerprint
    C  clone the snapshot
    D  backfill the clone
    E  backfill again -- must change nothing
    F  verify identity / privacy / forget / replay on the clone
    G  emit the migration plan
    H  --apply: only after a clean clone, backfill the real store
    I  independent read-back on a fresh connection
    J  the snapshot is never deleted, by this script or otherwise

Usage:
    python scripts/resolver2_migration.py --db <path>            # A-G, dry
    python scripts/resolver2_migration.py --db <path> --apply    # ...then H-I
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.slot_backfill import RESOLVER_VERSION, backfill
from bio_agent_os.evals.frozen import clone, digest, provenance

MIGRATION = "resolver2-clause-boundary"


# ---------------------------------------------------------------- measurement

def _slots(conn: sqlite3.Connection) -> list[tuple[str, dict, str]]:
    rows = []
    for memory_id, blob, content in conn.execute(
            "SELECT memory_id, structured_json, content FROM cognitive_memories"):
        try:
            slot = json.loads(blob) if blob else {}
        except (TypeError, ValueError):
            slot = {}
        rows.append((str(memory_id), slot if isinstance(slot, dict) else {},
                     str(content or "")))
    return rows


def census(db: Path) -> dict:
    """What this store looks like, without changing it."""
    conn = sqlite3.connect(str(db))
    try:
        rows = _slots(conn)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {}
        for table in tables:
            try:
                counts[table] = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.DatabaseError:
                pass
    finally:
        conn.close()

    with_slot = [s for _, s, _ in rows if s.get("attribute")]
    stale = [s for s in with_slot if s.get("resolver_version") != RESOLVER_VERSION]
    return {
        "memory_rows": len(rows),
        "rows_with_slot": len(with_slot),
        "rows_without_slot": len(rows) - len(with_slot),
        "rows_stale_resolver": len(stale),
        "rows_current_resolver": len(with_slot) - len(stale),
        "rows_with_entity": sum(1 for s in with_slot if s.get("entity")),
        "rows_entity_unknown": sum(1 for s in with_slot if not s.get("entity")),
        "distinct_entities": len({s["entity"] for s in with_slot
                                  if s.get("entity")}),
        "table_counts": counts,
    }


def identities(db: Path) -> dict[str, set[str]]:
    """entity surface -> the memory ids filed under it."""
    conn = sqlite3.connect(str(db))
    try:
        out: dict[str, set[str]] = {}
        for memory_id, slot, _ in _slots(conn):
            entity = slot.get("entity")
            if entity:
                out.setdefault(str(entity), set()).add(memory_id)
        return out
    finally:
        conn.close()


def merges_and_splits(before: dict[str, set[str]],
                      after: dict[str, set[str]]) -> dict:
    """Did migration join two people, or break one apart?

    A merge is the failure with no benign reading, so it is reported by name
    and never as a count alone. A split is reported the same way: one person
    under two identities is the defect this resolver change exists to close,
    and reintroducing it elsewhere would be the fix arriving as the bug.
    """
    owner_before: dict[str, str] = {}
    for surface, ids in before.items():
        for memory_id in ids:
            owner_before[memory_id] = surface
    owner_after: dict[str, str] = {}
    for surface, ids in after.items():
        for memory_id in ids:
            owner_after[memory_id] = surface

    moved: dict[tuple[str, str], list[str]] = {}
    for memory_id, was in owner_before.items():
        now = owner_after.get(memory_id)
        if now is not None and now != was:
            moved.setdefault((was, now), []).append(memory_id)

    # Two distinct surfaces before, one surface after, is a merge.
    targets: dict[str, set[str]] = {}
    for (was, now) in moved:
        targets.setdefault(now, set()).add(was)
    merges = {now: sorted(froms) for now, froms in targets.items()
              if len(froms | ({now} & set(before))) > 1
              and len(froms) > 1}
    # One surface before landing on more than one surface after is a split.
    sources: dict[str, set[str]] = {}
    for (was, now) in moved:
        sources.setdefault(was, set()).add(now)
    splits = {was: sorted(tos) for was, tos in sources.items() if len(tos) > 1}

    return {
        "rows_reassigned": sum(len(v) for v in moved.values()),
        "reassignments": [{"from": w, "to": n, "rows": len(ids)}
                          for (w, n), ids in sorted(moved.items())][:50],
        "merges": merges,
        "splits": splits,
    }


# ---------------------------------------------------------------- verification

def verify_clone(db: Path) -> dict:
    """Does the migrated clone still behave? Read-side, on a real MemoryOS."""
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.privacy_registry import undeclared

    checks: dict[str, object] = {}
    conn = sqlite3.connect(str(db))
    try:
        checks["undeclared_stores"] = sorted(undeclared(conn))
    except Exception as exc:                              # noqa: BLE001
        checks["undeclared_stores_error"] = repr(exc)
    finally:
        conn.close()

    memory_os = MemoryOS(db)
    try:
        T.reset_execution()
        subjects = [r[0] for r in memory_os.memories.conn.execute(
            "SELECT DISTINCT json_extract(structured_json, '$.entity') "
            "FROM cognitive_memories "
            "WHERE json_extract(structured_json, '$.entity') IS NOT NULL "
            "LIMIT 25")]
        answered = 0
        for subject in subjects:
            try:
                if T.claim_history(memory_os, subject=str(subject),
                                   aspect=None, context=None):
                    answered += 1
            except Exception as exc:                      # noqa: BLE001
                checks.setdefault("claim_history_errors", []).append(
                    f"{subject}: {exc!r}")
        checks["subjects_probed"] = len(subjects)
        checks["subjects_with_history"] = answered
        checks["execution"] = dict(T.execution_report())
    finally:
        memory_os.close()
    return checks


# ---------------------------------------------------------------------- driver

def main() -> int:
    ap = argparse.ArgumentParser("resolver2_migration")
    ap.add_argument("--db", required=True, help="the real store")
    ap.add_argument("--snapshots", default=None,
                    help="where snapshots live (default: <db dir>/snapshots)")
    ap.add_argument("--apply", action="store_true",
                    help="after a clean clone, migrate the real store")
    args = ap.parse_args()

    db = Path(args.db).resolve()
    if not db.exists():
        print(f"khong thay store: {db}")
        return 2
    snap_root = Path(args.snapshots) if args.snapshots else db.parent / "snapshots"
    stamp = time.strftime("%Y%m%dT%H%M%S")
    snap_dir = snap_root / f"{stamp}-pre-{MIGRATION}"

    # ---- A. snapshot, with the side files -------------------------------
    snap = snap_dir / db.name
    clone(db, snap)
    print(f"[A] snapshot -> {snap}")
    for suffix in ("-wal", "-shm"):
        if Path(str(snap) + suffix).exists():
            print(f"      kem {suffix}")

    # ---- B. hash, counts, fingerprint -----------------------------------
    before = census(db)
    prov = provenance(db.parent, source=snap, migration_version=MIGRATION)
    manifest = {
        "migration": MIGRATION,
        "resolver_version_target": RESOLVER_VERSION,
        "taken_at": stamp,
        "source_db": str(db),
        "snapshot": str(snap),
        "snapshot_sha256": digest(snap),
        "source_sha256": digest(db),
        "runtime_fingerprint": prov["runtime_fingerprint"],
        "census_before": before,
    }
    print(f"[B] sha256(store)    = {manifest['source_sha256'][:16]}…")
    print(f"    sha256(snapshot) = {manifest['snapshot_sha256'][:16]}…")
    print(f"    fingerprint      = {manifest['runtime_fingerprint']}")
    print(f"    rows={before['memory_rows']}  co slot={before['rows_with_slot']}"
          f"  stale={before['rows_stale_resolver']}"
          f"  khong entity={before['rows_entity_unknown']}")
    if manifest["source_sha256"] != manifest["snapshot_sha256"]:
        print("    ✗ snapshot khac store — store dang duoc ghi. DUNG LAI.")
        return 3

    # ---- C. clone --------------------------------------------------------
    work = snap_dir / f"clone-{db.name}"
    clone(snap, work)
    print(f"[C] clone -> {work}")

    # ---- D. backfill the clone ------------------------------------------
    ident_before = identities(work)
    conn = sqlite3.connect(str(work))
    try:
        first = backfill(conn)
    finally:
        conn.close()
    print(f"[D] backfill lan 1: rederived={first.rederived} "
          f"filled={first.filled} changed={first.changed} "
          f"entity_repairs={len(first.entity_repairs)}")

    # ---- E. idempotence --------------------------------------------------
    conn = sqlite3.connect(str(work))
    try:
        second = backfill(conn)
    finally:
        conn.close()
    idempotent = (second.rederived == 0 and second.changed == 0
                  and not second.entity_repairs)
    print(f"[E] backfill lan 2: rederived={second.rederived} "
          f"changed={second.changed} -> "
          f"{'idempotent ✓' if idempotent else 'KHONG idempotent ✗'}")

    # ---- F. verification -------------------------------------------------
    checks = verify_clone(work)
    print(f"[F] undeclared stores : {checks.get('undeclared_stores')}")
    print(f"    subjects probed   : {checks.get('subjects_probed')} "
          f"-> co lich su {checks.get('subjects_with_history')}")
    if checks.get("claim_history_errors"):
        print(f"    ✗ loi doc: {checks['claim_history_errors'][:3]}")

    # ---- G. plan ---------------------------------------------------------
    after = census(work)
    ident_after = identities(work)
    movement = merges_and_splits(ident_before, ident_after)
    manifest.update({
        "census_after_clone": after,
        "backfill_first": first.as_dict(),
        "backfill_second": second.as_dict(),
        "idempotent": idempotent,
        "verification": checks,
        "movement": movement,
        "clone": str(work),
        "clone_sha256": digest(work),
    })

    print("\n[G] MIGRATION PLAN")
    print(f"    total rows            {before['memory_rows']}")
    print(f"    rows eligible         {before['rows_stale_resolver']}")
    print(f"    rows changed          {first.changed}")
    print(f"    rows UNKNOWN (khong entity)  "
          f"{before['rows_entity_unknown']} -> {after['rows_entity_unknown']}")
    print(f"    stale resolver        {before['rows_stale_resolver']} -> "
          f"{after['rows_stale_resolver']}")
    print(f"    rows reassigned       {movement['rows_reassigned']}")
    print(f"    identity MERGES       {movement['merges'] or 'khong co'}")
    print(f"    identity SPLITS       {movement['splits'] or 'khong co'}")

    clean = (idempotent
             and not checks.get("claim_history_errors")
             and not checks.get("undeclared_stores")
             and not movement["merges"]
             and after["rows_stale_resolver"] == 0
             # A migration must not invent identities for rows that had none.
             and after["rows_entity_unknown"] >= before["rows_entity_unknown"]
             * 0)
    resolved_away = (before["rows_entity_unknown"]
                     - after["rows_entity_unknown"])
    if resolved_away > 0:
        print(f"    ✗ {resolved_away} hang khong co entity gio da co — "
              f"migration dang tu quyet ambiguity")
        clean = False
    manifest["clone_clean"] = bool(clean)

    report = snap_dir / "migration_plan.json"
    report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                 default=str), encoding="utf-8")
    print(f"\n    plan -> {report}")
    print(f"    CLONE {'SACH ✓' if clean else 'CHUA SACH ✗'}")

    if not args.apply:
        print("\n    dry-run. Them --apply de chay tren store that.")
        return 0 if clean else 1
    if not clean:
        print("\n    ✗ clone chua sach — KHONG dung vao store that.")
        return 1

    # ---- H. the real store ----------------------------------------------
    if digest(db) != manifest["source_sha256"]:
        print("\n    ✗ store da doi ke tu snapshot. DUNG LAI, chup lai.")
        return 3
    conn = sqlite3.connect(str(db))
    try:
        applied = backfill(conn)
    finally:
        conn.close()
    print(f"\n[H] store that: rederived={applied.rederived} "
          f"changed={applied.changed} entity_repairs={len(applied.entity_repairs)}")

    # ---- I. independent read-back, fresh connection ----------------------
    live = census(db)
    print(f"[I] doc lai (connection moi): stale={live['rows_stale_resolver']} "
          f"khong entity={live['rows_entity_unknown']}")
    ok = (live["rows_stale_resolver"] == 0
          and live["rows_entity_unknown"] == before["rows_entity_unknown"]
          and live["memory_rows"] == before["memory_rows"])
    manifest["applied"] = applied.as_dict()
    manifest["census_after_real"] = live
    manifest["after_sha256"] = digest(db)
    manifest["applied_clean"] = bool(ok)
    report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                 default=str), encoding="utf-8")
    print(f"    {'✓ khop voi clone' if ok else '✗ KHONG khop voi clone'}")
    print(f"\n[J] snapshot giu nguyen: {snap}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
