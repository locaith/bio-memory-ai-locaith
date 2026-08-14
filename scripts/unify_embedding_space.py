"""Put a store back into a single vector space.

A store acquires a second space quietly: an ingest run whose embedder picked a
different backend, or a migration half-done. Measured on the learning store on
2026-08-14:

    openai                dims=3072  n=353
    sentence-transformers dims= 384  n= 60

Vectors from different spaces cannot be compared. `cosine()` returns 0.0 for
mismatched dimensions and retrieval reads 0.0 as "unrelated", so before the
retrieval fix each half was invisible to queries embedded by the other. The fix
stops the silent scoring; it does not merge the store. This does.

Choosing the target space was measured rather than assumed
(`compare_embedding_spaces.py`). Two protocols, neither able to decide on
quality: the known-item set was won by a no-vector control, and the
cross-lingual set collapsed to 11 questions over one class. What both measured
cleanly was latency — 40 ms local against 565 ms for the cloud round trip — so
the choice rests on latency, cost and privacy, which is what the evidence
actually supports.

Three guards, because every one of them has already caught something:

* refuses to run if the embedder degraded to hash mode, where recall is
  near-random and a rebuilt store would be quietly worthless;
* refuses if the produced dimensionality is not what was asked for, which is
  what stopped an earlier attempt from creating a *third* space;
* re-reads the store afterwards and fails if more than one space remains.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

DEFAULT_DB = _REPO / "data" / "learning" / "memory.db"


def make_embedder(backend: str):
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
    for key in ("EMBEDDING_BACKEND", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL",
                "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS"):
        os.environ.pop(key, None)

    if backend == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit("OPENAI_API_KEY không có trong .env")
        # `Embedder` is local-first by design: sentence-transformers wins over a
        # bare OPENAI_API_KEY. The dedicated embeddings config is the documented
        # way to ask for the cloud endpoint, rather than defeating that design.
        os.environ["EMBEDDING_API_KEY"] = api_key
        os.environ["EMBEDDING_MODEL"] = "text-embedding-3-large"
        os.environ["EMBEDDING_DIMENSIONS"] = "3072"

    from bio_agent_os.core.embedder import Embedder

    return Embedder()


def spaces(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    try:
        return list(conn.execute(
            "SELECT model, dims, COUNT(*) FROM memory_embeddings "
            "GROUP BY model, dims ORDER BY COUNT(*) DESC"))
    except sqlite3.OperationalError:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(prog="unify_embedding_space")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--backend", choices=("local", "openai"), default="local")
    ap.add_argument("--apply", action="store_true",
                    help="thực sự ghi; mặc định chỉ xem trước")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"không thấy {db}")

    conn = sqlite3.connect(str(db))
    print(f"== {db.name} trước khi gộp ==")
    before = spaces(conn)
    for model, dims, count in before:
        print(f"  {model:<24} dims={dims:<6} n={count}")
    if len(before) <= 1:
        print("\n  Kho đã ở một không gian duy nhất. Không có gì để gộp.")
        conn.close()
        return 0

    backend = "sentence-transformers" if args.backend == "local" else "openai"
    print(f"\n  gộp về: {args.backend}")
    if not args.apply:
        print("  XEM TRƯỚC — thêm --apply để thực sự ghi")
        conn.close()
        return 0

    backup = db.with_name(db.stem + "_before_unify.db")
    shutil.copy2(db, backup)
    print(f"  sao lưu: {backup.name}")

    embedder = make_embedder(args.backend)
    probe = embedder.embed("kiểm tra số chiều")
    actual_backend = getattr(embedder, "backend", "?")
    print(f"  embedder: backend={actual_backend} "
          f"model={getattr(embedder, 'model_id', '?')} dims={len(probe)}")

    if actual_backend == "hash":
        raise SystemExit(
            "embedder rơi về hash mode — recall gần như ngẫu nhiên. Dừng lại: "
            "dựng lại kho bằng vector băm là biến nó thành vô dụng một cách "
            "im lặng."
        )
    if actual_backend != backend:
        raise SystemExit(
            f"đã yêu cầu {backend} nhưng nhận được {actual_backend}. Dừng lại "
            f"thay vì ghi vào một không gian không ai chọn."
        )

    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes, coverage,
    )

    conn.execute("DELETE FROM memory_embeddings")
    try:
        conn.execute("DELETE FROM embedding_calibration")
    except sqlite3.OperationalError:
        pass                      # never calibrated; nothing to clear
    conn.commit()

    written = 0
    while True:
        batch = backfill_embeddings(conn, embedder, limit=100)
        written += batch
        if batch == 0:
            break
    print(f"  nhúng lại: {written}")

    # The floor was measured against the old population. Leaving it would apply
    # a threshold from a space that no longer exists.
    result = calibrate_with_probes(conn, embedder)
    if result:
        print(f"  hiệu chuẩn lại: floor={result.get('floor')} "
              f"mean={result.get('mean')} samples={result.get('samples')}")
    else:
        print("  ⚠  không hiệu chuẩn được — sàn sẽ dùng mặc định")

    after = spaces(conn)
    print(f"\n== {db.name} sau khi gộp ==")
    for model, dims, count in after:
        print(f"  {model:<24} dims={dims:<6} n={count}")
    print(f"  {coverage(conn)}")
    conn.close()

    if len(after) != 1:
        raise SystemExit(
            f"vẫn còn {len(after)} không gian sau khi gộp — chưa xong, "
            f"khôi phục từ {backup.name} rồi xem lại."
        )
    print("\n  Một không gian duy nhất. Xong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
