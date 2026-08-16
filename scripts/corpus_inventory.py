"""What historical data exists, described — and nothing read into memory.

More than a year of real working history sits on this machine: Claude Code and
Codex transcripts, VS Code workspaces, project trees, customer work, the
decisions and the mistakes. It is the corpus a longitudinal study of this
system would need.

**It is not ingested here, and this tool cannot ingest it.** There is no write
path to `MemoryOS` in this file, on purpose and by design rather than by
discipline: a deletion in this system does not yet survive a replay
(`tests/test_replay_resurrection.py`), so anything taken in now could not be
reliably taken back out. Inventory first, ingest after that is fixed.

What is produced is a manifest: one row per source, describing shape and
sensitivity. Enough to plan a chronological split and to decide what may never
be ingested at all.

**Secrets are never copied.** A file that looks like it holds credentials gets
`contains_secret = true` and a count, and nothing else — not a sample, not a
redacted line, not the matching pattern's context. A manifest that quotes a
secret to prove it found one has published it. The same applies to personal and
customer data: the manifest records *that* a source holds it, never *what*.

Partitions are kept apart from the first touch, because separating them later
means reading them together first:

    PERSONAL / LOCAITH_INTERNAL / CUSTOMER / PROJECT / UNKNOWN
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


class Partition:
    PERSONAL = "PERSONAL"
    LOCAITH_INTERNAL = "LOCAITH_INTERNAL"
    CUSTOMER = "CUSTOMER"
    PROJECT = "PROJECT"
    UNKNOWN = "UNKNOWN"


#: Patterns that mean "this holds a credential".
#:
#: Deliberately broad and deliberately cheap. A false positive costs one source
#: marked as needing review; a false negative puts a key into a corpus that
#: later becomes training data or context. The asymmetry is not close.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai_key", r"sk-[A-Za-z0-9]{20,}"),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("google_key", r"AIza[0-9A-Za-z_\-]{35}"),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{30,}"),
    ("slack_token", r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    ("aws_key", r"AKIA[0-9A-Z]{16}"),
    ("private_key_block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("bearer", r"[Bb]earer\s+[A-Za-z0-9._\-]{20,}"),
    ("generic_assignment",
     r"(?i)(api[_-]?key|secret|password|passwd|token|credential)"
     r"\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    ("connection_string", r"(?i)(postgres|mysql|mongodb)(\+\w+)?://[^\s'\"]+:[^\s'\"]+@"),
)

#: Files that are credentials by name, whatever is inside them.
SECRET_FILENAMES = re.compile(
    r"(^\.env($|\.)|(^|[._-])secrets?\.(json|ya?ml|toml|txt)$"
    r"|id_rsa|id_ed25519|\.pem$|\.pfx$|\.p12$|credentials\.json$"
    r"|service[_-]account.*\.json$)", re.IGNORECASE)

#: Weak signals for personal and customer content. Counted, never quoted.
PERSONAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("vn_phone", r"(?<!\d)0[35789]\d{8}(?!\d)"),
    ("email", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ("vn_id", r"(?<!\d)\d{12}(?!\d)"),
    ("bank_account", r"(?i)(số tài khoản|stk)\s*[:\-]?\s*\d{6,}"),
)

TEXTUAL = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".py", ".ts",
           ".tsx", ".js", ".jsx", ".sql", ".sh", ".ps1", ".toml", ".ini",
           ".cfg", ".html", ".css", ".env", ".log", ".csv"}

#: Never opened. Reading them yields nothing a manifest can use and costs time.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", ".next", ".cache", ".pytest_cache", "site-packages",
             ".mypy_cache", ".ruff_cache", "target", ".gradle"}

#: How much of a file to read when looking for secrets. A key in the first
#: 256 KB is a key; a manifest is not a security scanner and should not
#: pretend to be one by reading gigabytes.
SCAN_BYTES = 256 * 1024


@dataclass
class Source:
    source_id: str
    source_path: str
    tool: str
    partition: str
    data_type: str
    file_count: int = 0
    bytes: int = 0
    first_modified: str = ""
    last_modified: str = ""
    project: str = ""
    customer: str = ""
    #: Counts only. What matched is never recorded.
    contains_secret: bool = False
    secret_hits: dict = field(default_factory=dict)
    contains_personal_data: bool = False
    personal_hits: dict = field(default_factory=dict)
    contains_customer_data: bool = False
    sensitivity: str = "unknown"
    provenance_quality: str = "unknown"
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _tool_of(path: Path) -> tuple[str, str, str]:
    """(tool, data_type, provenance_quality) from where a thing lives."""
    parts = {p.lower() for p in path.parts}
    name = path.name.lower()
    if ".claude" in parts or "claude" in name:
        return "claude-code", "agent_transcript", "high"
    if "codex" in parts or "codex" in name:
        return "codex", "agent_transcript", "high"
    if ".vscode" in parts or "vscode" in name:
        return "vscode", "editor_state", "medium"
    if ".git" in parts:
        return "git", "vcs_history", "high"
    if any(p.endswith(".ipynb") for p in (name,)):
        return "jupyter", "notebook", "medium"
    return "filesystem", "project_tree", "low"


def _partition_of(path: Path, customers: tuple[str, ...]) -> tuple[str, str]:
    """(partition, customer). Errs toward UNKNOWN rather than guessing.

    A source filed under the wrong tenant is worse than one filed under none:
    UNKNOWN gets reviewed, and a confident wrong label does not.
    """
    lowered = str(path).lower()
    for customer in customers:
        if customer.lower() in lowered:
            return Partition.CUSTOMER, customer
    if "locaith" in lowered:
        return Partition.LOCAITH_INTERNAL, ""
    if any(marker in lowered for marker in
           ("\\documents\\", "/documents/", "personal", "ca nhan", "cá nhân")):
        return Partition.PERSONAL, ""
    return Partition.UNKNOWN, ""


def _scan_text(text: str, patterns) -> dict[str, int]:
    hits: dict[str, int] = {}
    for label, pattern in patterns:
        found = len(re.findall(pattern, text))
        if found:
            hits[label] = found
    return hits


def inspect(root: Path, *, customers: tuple[str, ...],
            max_files: int) -> Source:
    tool, data_type, provenance = _tool_of(root)
    partition, customer = _partition_of(root, customers)
    source = Source(
        source_id=hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16],
        source_path=str(root), tool=tool, partition=partition,
        data_type=data_type, project=root.name, customer=customer,
        provenance_quality=provenance)

    secret_hits: Counter = Counter()
    personal_hits: Counter = Counter()
    oldest = newest = None
    seen = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            source.file_count += 1
            source.bytes += stat.st_size
            when = stat.st_mtime
            oldest = when if oldest is None else min(oldest, when)
            newest = when if newest is None else max(newest, when)

            if SECRET_FILENAMES.search(filename):
                secret_hits["credential_file"] += 1
                source.notes.append(
                    f"tệp mang tên chứng thực: {filename} (KHÔNG mở, KHÔNG đọc)")
                continue

            if seen >= max_files or path.suffix.lower() not in TEXTUAL:
                continue
            try:
                text = path.open("r", encoding="utf-8", errors="ignore").read(SCAN_BYTES)
            except OSError:
                continue
            seen += 1
            secret_hits.update(_scan_text(text, SECRET_PATTERNS))
            personal_hits.update(_scan_text(text, PERSONAL_PATTERNS))

    if oldest is not None:
        source.first_modified = datetime.fromtimestamp(
            oldest, timezone.utc).date().isoformat()
        source.last_modified = datetime.fromtimestamp(
            newest, timezone.utc).date().isoformat()

    source.secret_hits = dict(secret_hits)
    source.contains_secret = bool(secret_hits)
    source.personal_hits = dict(personal_hits)
    source.contains_personal_data = bool(personal_hits)
    source.contains_customer_data = partition == Partition.CUSTOMER
    source.sensitivity = (
        "secret" if source.contains_secret else
        "customer" if source.contains_customer_data else
        "personal" if source.contains_personal_data else
        "internal" if partition == Partition.LOCAITH_INTERNAL else "unknown")
    if seen >= max_files:
        source.notes.append(
            f"chỉ mở {max_files} tệp đầu để dò — số liệu nhạy cảm là SÀN, "
            f"không phải tổng")
    return source


def split_proposal(sources: list[Source]) -> dict:
    """A chronological split, proposed and not applied.

    By time rather than at random, because the question the corpus is meant to
    answer is whether the system improves *with experience*. A random split
    lets a lesson learned in June be evaluated on a task from March, which
    measures memorisation and calls it learning.

    The held-out third is named and then left alone. Anything that tunes
    architecture or thresholds against it stops it being held out, and there is
    no way to undo that except by collecting another year.
    """
    dated = sorted([s for s in sources if s.first_modified],
                   key=lambda s: s.first_modified)
    if not dated:
        return {"error": "không nguồn nào có mốc thời gian"}
    start, end = dated[0].first_modified, max(s.last_modified for s in dated)
    days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
    first_cut = (datetime.fromisoformat(start).timestamp() + days * 0.6 * 86400)
    second_cut = (datetime.fromisoformat(start).timestamp() + days * 0.8 * 86400)
    return {
        "basis": "theo thời gian, không ngẫu nhiên",
        "span_days": days,
        "development": {"from": start,
                        "to": datetime.fromtimestamp(first_cut,
                                                     timezone.utc).date().isoformat(),
                        "share": 0.6},
        "validation": {"from": datetime.fromtimestamp(first_cut,
                                                      timezone.utc).date().isoformat(),
                       "to": datetime.fromtimestamp(second_cut,
                                                    timezone.utc).date().isoformat(),
                       "share": 0.2},
        "frozen_heldout": {"from": datetime.fromtimestamp(
                               second_cut, timezone.utc).date().isoformat(),
                           "to": end, "share": 0.2,
                           "rule": "không kiến trúc, không tuning, không đọc "
                                   "cho tới khi có bài đo đã khoá"},
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="corpus_inventory")
    ap.add_argument("roots", nargs="*", default=[],
                    help="thư mục cần mô tả. Không đưa gì thì chỉ in gợi ý.")
    ap.add_argument("--customer", action="append", default=[],
                    help="tên khách hàng để tách phân vùng, lặp lại được")
    ap.add_argument("--max-files", type=int, default=400,
                    help="số tệp mở tối đa mỗi nguồn khi dò nhạy cảm")
    ap.add_argument("--out", default="benchmark_reports/corpus_manifest.json")
    args = ap.parse_args()

    print("KIỂM KÊ CORPUS — CHỈ MÔ TẢ, KHÔNG NẠP VÀO BỘ NHỚ")
    print("=" * 70)
    print("  Tệp này không có đường ghi nào tới MemoryOS.")
    print("  Bí mật chỉ được ĐẾM, không trích, không mẫu, không ngữ cảnh.")
    print("  Chưa nạp gì cho tới khi xoá sống sót qua replay.\n")

    if not args.roots:
        print("  Chưa chỉ thư mục nào. Ví dụ:")
        print("    python scripts/corpus_inventory.py "
              "~/.claude/projects C:/locaith --customer ARCHILAB")
        return 0

    sources = []
    for raw in args.roots:
        root = Path(raw).expanduser()
        if not root.exists():
            print(f"  bỏ qua (không tồn tại): {root}")
            continue
        print(f"  đang mô tả: {root}")
        sources.append(inspect(root, customers=tuple(args.customer),
                               max_files=args.max_files))

    if not sources:
        print("\n  không nguồn nào đọc được")
        return 1

    print("\n" + "=" * 70)
    print("NGUỒN")
    print("=" * 70)
    for source in sources:
        print(f"\n  {source.source_path}")
        print(f"    id={source.source_id}  công cụ={source.tool}  "
              f"loại={source.data_type}")
        print(f"    phân vùng={source.partition}"
              f"{'  khách=' + source.customer if source.customer else ''}")
        print(f"    {source.file_count} tệp, {source.bytes/1e6:.1f} MB, "
              f"{source.first_modified} → {source.last_modified}")
        print(f"    nhạy cảm={source.sensitivity}  "
              f"provenance={source.provenance_quality}")
        if source.contains_secret:
            print(f"    ⛔ CHỨA BÍ MẬT: {source.secret_hits}  "
                  f"— KHÔNG BAO GIỜ NẠP nguyên trạng")
        if source.contains_personal_data:
            print(f"    ⚠ dữ liệu cá nhân: {source.personal_hits}")
        for note in source.notes[:5]:
            print(f"    · {note}")

    print("\n" + "=" * 70)
    print("TỔNG HỢP")
    print("=" * 70)
    by_partition = Counter(s.partition for s in sources)
    print(f"  theo phân vùng: {dict(by_partition)}")
    print(f"  tổng: {sum(s.file_count for s in sources)} tệp, "
          f"{sum(s.bytes for s in sources)/1e9:.2f} GB")
    unsafe = [s for s in sources if s.contains_secret]
    print(f"  nguồn chứa bí mật: {len(unsafe)}/{len(sources)}")
    unknown = [s for s in sources if s.partition == Partition.UNKNOWN]
    print(f"  chưa xác định được phân vùng: {len(unknown)}/{len(sources)}"
          f"  — cần người xếp trước khi nạp")

    split = split_proposal(sources)
    print("\n" + "=" * 70)
    print("ĐỀ XUẤT CHIA THEO THỜI GIAN — đề xuất, chưa áp dụng")
    print("=" * 70)
    for key in ("development", "validation", "frozen_heldout"):
        part = split.get(key)
        if isinstance(part, dict):
            print(f"  {key:<16} {part.get('from')} → {part.get('to')}"
                  f"  ({part.get('share')})")
    print("  Chia theo thời gian chứ không ngẫu nhiên: bài toán là hệ có khá")
    print("  lên NHỜ TRẢI NGHIỆM không, mà chia ngẫu nhiên cho phép một bài")
    print("  học tháng 6 được chấm trên việc tháng 3 — đó là đo thuộc lòng.")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    from bio_agent_os.core.provenance import identity
    out.write_text(json.dumps({
        "created": datetime.now(timezone.utc).isoformat(),
        "runtime": identity().as_dict(),
        "ingested": False,
        "ingest_blocked_by": "tests/test_replay_resurrection.py — xoá chưa "
                             "sống sót qua replay, nên chưa nạp gì rút ra "
                             "được một cách chắc chắn",
        "sources": [s.as_dict() for s in sources],
        "split_proposal": split,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    print("  Chưa có gì được nạp vào bộ nhớ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
