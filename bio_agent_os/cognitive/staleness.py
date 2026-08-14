"""How old is this memory, and does its age change what it means?

Measured 2026-08-14 on the behaviour benchmark: **both** this system and plain
RAG scored 0/3 on stale memories. Asked "what is the price now?" against a
memory reading "6,500,000 VND, last updated March 2026", both answered
"6,500,000 VND" — flatly, as though it were today's price.

Nobody answers this well, including the competitors, and the reason is
structural rather than clever: the age is never in the context. `observed_at`
sits on every memory and retrieval throws it away, so the model formatting an
answer has nothing to caveat with. It is not being careless; it was never told.

This puts the age back. Two pieces, both deliberately dumb:

* `age_days` — how long ago it was recorded.
* `time_sensitive` — whether the *content* is the kind of claim that expires.
  A price, a balance, a status, a headcount go out of date. "SQLite only
  returns file space on TRUNCATE" does not, and stamping a caveat on a
  permanent fact trains the reader to ignore caveats.

The system reports; the caller decides. Nothing here rewrites an answer or
suppresses a memory — a memory that is old is still the best answer available,
and hiding it would be worse than dating it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

#: Claims whose truth has a shelf life. Deliberately narrow: over-marking is the
#: failure mode that makes a caveat worthless, so anything not obviously
#: perishable is left alone.
_PERISHABLE = re.compile(
    r"(giá|đơn giá|chi phí|doanh thu|số dư|công nợ|lương|ngân sách|tồn kho"
    r"|hiện tại|đang|tính đến|cập nhật|trạng thái|giai đoạn|tiến độ"
    r"|phụ trách|đảm nhiệm|version|phiên bản|cổng \d+"
    r"|price|cost|revenue|balance|salary|budget|status|currently|as of"
    r"|headcount|stock|owner|assigned)",
    re.IGNORECASE,
)

#: Beyond this, an answer that does not mention when it was recorded is
#: misleading rather than merely terse. Ninety days is a quarter — long enough
#: that a price, a status or an owner has plausibly moved.
DEFAULT_STALE_AFTER_DAYS = 90.0


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            parsed = parser(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_time_sensitive(content: str) -> bool:
    return bool(_PERISHABLE.search(str(content or "")))


def age_days(memory: Any, now: datetime | None = None) -> float | None:
    """Days since the memory was recorded, or None if it never said."""
    stamp = _parse(getattr(memory, "observed_at", None)) or \
        _parse(getattr(memory, "created_at", None))
    if stamp is None:
        return None
    reference = now or datetime.now(timezone.utc)
    return round((reference - stamp).total_seconds() / 86400.0, 2)


def describe(memory: Any, *, stale_after_days: float = DEFAULT_STALE_AFTER_DAYS,
             now: datetime | None = None) -> dict[str, Any]:
    """The age facts about one memory. Reports; never decides."""
    days = age_days(memory, now)
    sensitive = is_time_sensitive(getattr(memory, "content", ""))
    stamp = _parse(getattr(memory, "observed_at", None)) or \
        _parse(getattr(memory, "created_at", None))
    return {
        "age_days": days,
        "recorded_at": stamp.date().isoformat() if stamp else None,
        "time_sensitive": sensitive,
        "stale": bool(sensitive and days is not None and days >= stale_after_days),
    }


def annotate(content: str, info: dict[str, Any]) -> str:
    """Put the date where whoever writes the answer will see it.

    Only for claims that can expire. A permanent fact carrying "(recorded
    2026-03-01)" adds noise, and noise is how a caveat stops being read.
    """
    if not info.get("time_sensitive") or not info.get("recorded_at"):
        return content
    return f"{content}  [ghi nhận {info['recorded_at']}]"


__all__ = [
    "DEFAULT_STALE_AFTER_DAYS",
    "age_days",
    "annotate",
    "describe",
    "is_time_sensitive",
]
