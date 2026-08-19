"""Sinh bảng luật trong CORE_INTEGRITY_CLOSEOUT.md TỪ manifest.

Tài liệu và dữ liệu trôi khỏi nhau là cách một hiến pháp âm thầm hết hiệu lực:
manifest sửa một chỗ, văn bản vẫn kể chuyện cũ, và người đọc tin văn bản. Nên
bảng được SINH RA, và `--check` khiến CI đỏ nếu ai sửa tay.

    python tools/render_core_integrity_laws.py           # ghi lại bảng
    python tools/render_core_integrity_laws.py --check    # chỉ kiểm, không ghi
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "CORE_INTEGRITY_CLOSEOUT.md"
MANIFEST = ROOT / "core_integrity_manifest.json"
BEGIN = "<!-- LAWS:BEGIN"
END = "<!-- LAWS:END -->"


def render(manifest: dict) -> str:
    by_domain: dict[str, list] = {}
    for law in manifest["laws"]:
        by_domain.setdefault(law["domain"], []).append(law)

    out: list[str] = []
    total = len(manifest["laws"])
    with_mutant = sum(1 for x in manifest["laws"] if x.get("has_mutant"))
    tests = {t for x in manifest["laws"] for t in x["enforcing_tests"]}
    out.append(f"**{total} điều luật** · {with_mutant} điều có mutant canh · "
               f"{len(tests)} test được trích dẫn · "
               f"{len(by_domain)} vùng.\n")

    for domain in sorted(by_domain):
        out.append(f"\n### {domain}\n")
        for law in by_domain[domain]:
            mark = " · **mutant**" if law.get("has_mutant") else ""
            out.append(f"#### {law['law']}{mark}\n")
            out.append(f"{law['why_it_exists']}\n")
            out.append(f"- **sự cố gốc** — {law['root_cause_incident']}")
            for t in law["enforcing_tests"]:
                out.append(f"- **test** — `{t}`")
            for r in law["runtime_location"]:
                out.append(f"- **runtime** — `{r}`")
            out.append("")
    return "\n".join(out)


def main(check: bool = False) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = DOC.read_text(encoding="utf-8")
    b = text.index(BEGIN)
    b_end = text.index("-->", b) + 3
    e = text.index(END)
    body = "\n" + render(manifest) + "\n"
    new = text[:b_end] + body + text[e:]
    if check:
        same = new == text
        print("bảng luật khớp manifest" if same
              else "LỆCH — chạy lại render, đừng sửa tay bảng luật")
        return 0 if same else 1
    DOC.write_text(new, encoding="utf-8")
    print(f"đã sinh bảng cho {len(manifest['laws'])} luật")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(check="--check" in sys.argv))
