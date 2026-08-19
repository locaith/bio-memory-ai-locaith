"""Kiểm manifest Core Integrity — biến hiến pháp thành thứ máy kiểm được.

    NO PROSE-ONLY CONSTITUTION.

Một điều luật chỉ có giá trị khi ba trích dẫn của nó GIẢI ĐƯỢC:

    root_cause_incident  → commit thật / file thật trong repo
    enforcing_tests      → node id pytest THU THẬP ĐƯỢC (và chạy được)
    runtime_location     → file:symbol tồn tại trong code đang ship

Chạy:
    python tools/verify_core_integrity_manifest.py [--run-tests]

Trả mã 1 nếu bất kỳ trích dẫn nào không giải được. Một hiến pháp trỏ vào chỗ
trống thì tệ hơn không có hiến pháp: nó tạo cảm giác được bảo vệ.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):        # console Windows mac dinh cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "core_integrity_manifest.json"


#: File nào không đọc được thì PHẢI ồn ào. Bản đầu nuốt `SyntaxError` thành
#: "không có hàm nào", nên một file mở đầu bằng BOM (U+FEFF) làm `ast.parse`
#: chết và bộ kiểm báo bốn trích dẫn ĐÚNG là sai. Instrument mù, không phải
#: bằng chứng hỏng — đúng họ lỗi mà cả dự án này trả học phí nhiều nhất.
_UNPARSEABLE: list[str] = []


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError) as exc:
        _UNPARSEABLE.append(f"{path.relative_to(ROOT)}: {exc}")
        return None


def _test_functions(path: Path) -> set[str]:
    """Tên hàm test khai báo trong file — đọc bằng AST, không bằng regex."""
    tree = _parse(path)
    if tree is None:
        return set()
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _module_symbols(path: Path) -> set[str]:
    """Symbol cấp module + method, dạng `name` và `Class.method`."""
    tree = _parse(path)
    if tree is None:
        return set()
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.ClassDef):
            out.add(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.add(f"{node.name}.{sub.name}")
                    out.add(sub.name)
                elif isinstance(sub, ast.AnnAssign) and isinstance(
                        sub.target, ast.Name):
                    out.add(f"{node.name}.{sub.target.id}")
                    out.add(sub.target.id)
                elif isinstance(sub, ast.Assign):
                    # thành viên Enum và hằng lớp cũng là symbol có thật —
                    # bản đầu chỉ đọc AnnAssign nên `ReplayReason.SKIP_ROW_LOST`
                    # bị báo "không tồn tại" trong khi nó nằm ngay đó.
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            out.add(f"{node.name}.{t.id}")
                            out.add(t.id)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _anchors(text: str) -> list[str]:
    """Rút ra các NEO có thể kiểm: hash commit hoặc đường dẫn."""
    import re
    out = re.findall(r"\b[0-9a-f]{7,40}\b", text)
    out += re.findall(r"[\w./\\-]+\.(?:py|md|json|jsonl|yaml|yml|toml)", text)
    return out


def _known_commits() -> set[str]:
    try:
        out = subprocess.run(["git", "log", "--format=%H"], cwd=ROOT,
                             capture_output=True, timeout=120)
        return {h.strip() for h in
                out.stdout.decode("utf-8", "replace").splitlines() if h.strip()}
    except Exception:                                        # noqa: BLE001
        return set()


_COMMITS: set[str] = set()


def _resolves(anchor: str, subjects: set[str]) -> bool:
    global _COMMITS
    if not _COMMITS:
        _COMMITS = _known_commits()
    if any(h.startswith(anchor) for h in _COMMITS):
        return True
    return (ROOT / anchor.replace("\\", "/")).exists()


def _commit_subjects() -> set[str]:
    try:
        out = subprocess.run(["git", "log", "--format=%s"], cwd=ROOT,
                             capture_output=True, timeout=120)
        return {line.strip() for line in
                out.stdout.decode("utf-8", "replace").splitlines() if line.strip()}
    except Exception:                                        # noqa: BLE001
        return set()


def main(run_tests: bool = False) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    laws = manifest["laws"]
    subjects = _commit_subjects()
    problems: list[str] = []
    gaps: list[str] = []
    tests_seen: set[str] = set()

    for law in laws:
        name = law["law"][:60]

        # 1. incident — phải NEO vào một thứ có thật: hash commit tồn tại,
        # hoặc một đường dẫn tồn tại. Văn xuôi mô tả sự cố thì hay, nhưng nó
        # không phải bằng chứng; bằng chứng là thứ mở ra được.
        inc = str(law.get("root_cause_incident", ""))
        anchors = _anchors(inc)
        ok_inc = any(_resolves(a, subjects) for a in anchors) or any(
            s and (s in inc) for s in subjects)
        if not ok_inc:
            problems.append(
                f"[{name}] incident không neo vào commit/file có thật: "
                f"{inc[:90]!r}")

        # 2. test — node id phải THU THẬP ĐƯỢC
        #
        # Yêu cầu KHÁC NHAU theo loại luật, và nói thẳng ra thay vì ép mọi
        # thứ vào một khuôn rồi hoặc là bỏ luật, hoặc là bịa một trích dẫn:
        #   runtime     — sống trong code đang ship: cần test + vị trí runtime
        #   measurement — ràng buộc CÁCH ĐO: cần test, không có chỗ trong runtime
        #   procedure   — thủ tục vận hành: enforce bởi harness, test tuỳ
        # Một luật `runtime` không có test mà KHÔNG khai `gap` thì là lỗ hổng
        # bị giấu — đó mới là thứ phải đỏ.
        kind = law.get("kind", "runtime")
        if not law.get("enforcing_tests"):
            if kind == "procedure":
                pass
            elif law.get("gap"):
                gaps.append(f"[{name}] {law['gap'][:110]}")
            else:
                problems.append(f"[{name}] không có test nào bảo vệ")
        for node in law.get("enforcing_tests", []):
            tests_seen.add(node)
            if "::" not in node:
                problems.append(f"[{name}] node id sai dạng: {node!r}")
                continue
            file_part, func = node.split("::", 1)
            path = ROOT / file_part
            if not path.exists():
                problems.append(f"[{name}] thiếu file test: {file_part}")
                continue
            func_base = func.split("[")[0]
            if func_base not in _test_functions(path):
                problems.append(
                    f"[{name}] {file_part} không khai báo {func_base}")

        # 3. runtime — luật phải sống trong code đang ship
        if not law.get("runtime_location") and kind != "measurement":
            problems.append(f"[{name}] không chỉ được nơi thực thi trong runtime")
        for loc in law.get("runtime_location", []):
            if ":" not in loc:
                problems.append(f"[{name}] runtime_location sai dạng: {loc!r}")
                continue
            file_part, symbol = loc.rsplit(":", 1)
            path = ROOT / file_part
            if not path.exists():
                problems.append(f"[{name}] thiếu file runtime: {file_part}")
                continue
            if symbol not in _module_symbols(path):
                problems.append(f"[{name}] {file_part} không có symbol {symbol}")

    for u in _UNPARSEABLE:
        problems.append(f"[bộ kiểm] không đọc nổi file được trích dẫn: {u}")

    kinds: dict = {}
    for law in laws:
        k = law.get("kind", "runtime")
        kinds[k] = kinds.get(k, 0) + 1
    print(f"laws: {len(laws)} {kinds}  ·  test citations: {len(tests_seen)}  "
          f"·  vấn đề: {len(problems)}  ·  lỗ hổng đã khai: {len(gaps)}")
    for g in gaps:
        print(f"   ! {g}")
    for p in problems:
        print(f"   ✗ {p}")

    if run_tests and not problems:
        nodes = sorted(tests_seen)
        print(f"\nchạy {len(nodes)} test được trích dẫn...")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", *nodes],
                           cwd=ROOT, capture_output=True, timeout=3600)
        tail = r.stdout.decode("utf-8", "replace").strip().splitlines()[-1:]
        print("   " + (tail[0] if tail else "(không có output)"))
        if r.returncode != 0:
            problems.append("test được trích dẫn KHÔNG xanh")

    print("\nMANIFEST:", "RESOLVED" if not problems else "KHÔNG GIẢI ĐƯỢC")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main(run_tests="--run-tests" in sys.argv))
