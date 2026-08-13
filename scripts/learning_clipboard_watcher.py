"""Turns a copied JSON block into a file in the learning inbox.

The capture side is a browser extension: it can read the course and write JSON
into the chat, and it cannot touch this machine's disk. Over ten thousand
lessons, "paste it into a file yourself" is not a workflow.

So this watches the clipboard. Copy the JSON block Claude prints, and the file
appears in the inbox — one Ctrl+C per lesson and nothing else. No connector to
configure, no API, nothing to keep logged in.

    python scripts/learning_clipboard_watcher.py

Accepts one object or an array of them, so a session can be batched into a
single copy. Anything that is not `locaith.learning.v1` is ignored in silence —
the clipboard is a shared surface and this must not react to ordinary copying.

Duplicates are dropped by content hash, because the same block gets copied twice
more often than not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

INBOX = Path(r"C:\locaith\learning-inbox")
SCHEMA = "locaith.learning.v1"
REQUIRED = ("schema", "course", "lesson_id", "summary")


def read_clipboard() -> str:
    """stdlib only — tkinter is present in a standard CPython install, and one
    more dependency for a helper script is one more thing to break."""
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""


def extract_json_blocks(text: str) -> list[dict]:
    """Find JSON in a chat message, fenced or bare."""
    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        candidates.append(match.group(1))
    candidates.append(text)

    found: list[dict] = []
    for blob in candidates:
        blob = blob.strip()
        if not blob or blob[0] not in "[{":
            continue
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for item in (parsed if isinstance(parsed, list) else [parsed]):
            if isinstance(item, dict) and item.get("schema") == SCHEMA:
                found.append(item)
        if found:
            break            # a fenced block that parsed is the intended one
    return found


def problems(record: dict) -> list[str]:
    """What is wrong with this record, stated rather than guessed at.

    A missing field is never filled in with a default: a guessed value becomes
    a memory nobody can tell apart from a real one.
    """
    issues = []
    for field in REQUIRED:
        if not str(record.get(field, "")).strip():
            issues.append(f"thiếu hoặc rỗng: {field}")
    for field in ("key_facts", "verbatim_excerpts", "qa"):
        if field in record and not isinstance(record[field], list):
            issues.append(f"{field} phải là danh sách")
    for index, pair in enumerate(record.get("qa") or []):
        if not isinstance(pair, dict) or not pair.get("question") or not pair.get("answer"):
            issues.append(f"qa[{index}] phải có cả question và answer")
    confidence = record.get("confidence")
    if confidence is not None and not (isinstance(confidence, (int, float)) and 0 <= confidence <= 1):
        issues.append("confidence phải là số trong khoảng 0..1")
    return issues


def slug(value: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value)).strip("-")
    return (cleaned or "unknown")[:limit]


def save(record: dict, inbox: Path) -> tuple[Path, list[str]]:
    issues = problems(record)
    target_dir = inbox if not issues else inbox / "_rejected"
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{slug(record.get('course'))}__{slug(record.get('lesson_id'))}__{stamp}.json"
    path = target_dir / name
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if issues:
        # Say what is wrong, in the same folder, so it can be fixed rather than
        # rediscovered. Silent rejection is how an inbox quietly stops working.
        path.with_suffix(".why.txt").write_text(
            "File bị từ chối vì:\n" + "\n".join(f"  - {i}" for i in issues) +
            "\n\nSửa rồi copy lại bản mới. Không sửa file này.\n",
            encoding="utf-8",
        )
    return path, issues


def main() -> int:
    ap = argparse.ArgumentParser(prog="learning_clipboard_watcher")
    ap.add_argument("--inbox", default=str(INBOX))
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--once", action="store_true", help="read the clipboard once and exit")
    args = ap.parse_args()

    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    for path in list(inbox.rglob("*.json")):
        try:
            seen.add(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError:
            pass

    print(f"  đang theo dõi clipboard -> {inbox}")
    print(f"  đã có sẵn {len(seen)} file. Copy khối JSON là file tự xuất hiện.")
    print("  Ctrl+C để dừng.\n", flush=True)

    last_clip = ""
    try:
        while True:
            clip = read_clipboard()
            if clip and clip != last_clip:
                last_clip = clip
                for record in extract_json_blocks(clip):
                    digest = hashlib.sha256(
                        json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest()
                    if digest in seen:
                        continue
                    seen.add(digest)
                    path, issues = save(record, inbox)
                    mark = "TỪ CHỐI" if issues else "nhận   "
                    qa = len(record.get("qa") or [])
                    facts = len(record.get("key_facts") or [])
                    print(f"  {mark} {path.name}   {facts} fact, {qa} câu hỏi"
                          + (f"   <- {issues[0]}" if issues else ""), flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  đã dừng.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
