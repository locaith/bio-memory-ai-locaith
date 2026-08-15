"""Behaviours LoCoMo cannot test: contradiction, staleness, forgetting, absence.

LoCoMo asks 300 recall questions and every one of them is answerable. That makes
it blind to most of what a memory has to get right in production:

    contradiction   two memories disagree — which one is current?
    stale           the fact was true in June; is it true now?
    forgetting      the user asked for something to be deleted
    absence         the answer is simply not there
    isolation       customer A's data must never surface for customer B
    provenance      a guess and an invoice are not the same kind of fact

Each case states what the answer **must** contain and what it **must not**. The
must-not is the half that matters: a memory that produces a plausible number for
a question it has no data on fails in a way that recall benchmarks score as
success.

    python scripts/behaviour_benchmark.py --systems cognitive,naive-rag

Cases live in `tests/fixtures/memory_behaviours.jsonl`, one JSON object per line.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

CASES = _REPO / "tests" / "fixtures" / "memory_behaviours.jsonl"

DECLINED = ("no information", "không có thông tin", "không biết", "không tìm thấy",
            "i don't know", "i do not know", "không rõ")

WORKSPACE_TAG = re.compile(r"^\s*\[workspace=(\w+)\]\s*")
DELETE_TAG = re.compile(r"^\s*\[XO[ÁA]\]\s*", re.IGNORECASE)


def load_cases() -> list[dict]:
    out = []
    for line in CASES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if item.get("id"):                 # skip the header/about line
            out.append(item)
    return out


def declined(text: str) -> bool:
    low = str(text or "").strip().lower()
    return (not low) or any(m in low for m in DECLINED)


def matches_delete_request(text: str, requests: set[str]) -> bool:
    """Does this memory fall under one of the deletion requests?

    A deletion request names a subject, not an exact string. "[XOÁ] dữ liệu
    khách hàng An Phát" has to take the phone number with it, so the match is on
    the request's content words appearing in the memory — deleting only the
    literal match is the bug these cases exist to catch.

    Module level and importable, because the integrity tests check the harness
    and a matcher hidden inside a closure cannot be checked at all.
    """
    low = str(text or "").lower()
    for request in requests:
        words = [w for w in str(request).lower().split() if len(w) > 3]
        if words and sum(1 for w in words if w in low) / len(words) >= 0.5:
            return True
    return False


def _holds(memory_os, needle: str) -> bool:
    """Is this string actually in the store right now?

    Called before a deletion so that "not found" afterwards means something.
    Without this the harness cannot tell a successful delete from data that was
    never ingested, and for three cases it did not.
    """
    if not needle:
        return False
    rows = memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        (f"%{needle}%",),
    ).fetchone()
    return bool(rows and rows[0])


def judge(case: dict, answer: str) -> tuple[bool, str]:
    """Pass only if every `must` appears and no `must_not` does.

    Deliberately mechanical. A model judging a model is how a benchmark starts
    agreeing with itself, and these cases are written so that a plain string
    check is enough.
    """
    low = str(answer or "").lower()

    for forbidden in case.get("must_not", []):
        token = str(forbidden).lower()
        # Free-text entries like "bịa ra một cái tên" describe a behaviour, not
        # a string; those are checked by the absence rule below instead.
        if len(token.split()) <= 3 and token in low:
            return False, f"chứa thứ bị cấm: {forbidden!r}"

    required = case.get("must", [])
    if not required:
        # An absence/forgetting case: the only correct answer is not answering.
        return (True, "từ chối đúng") if declined(answer) else (False, "đáng lẽ phải nói không biết")

    missing = [m for m in required if str(m).lower() not in low]
    if not missing:
        return True, "đạt"

    # Some questions have two honest answers. "What is the price now?" against a
    # memory that only knows March can be answered by citing March or by saying
    # it does not know — both are truthful, and only stating the March figure as
    # today's price is wrong. Cases that say so carry `accept_decline`.
    if case.get("accept_decline") and declined(answer):
        return True, "từ chối — cũng là câu trả lời trung thực"

    return False, f"thiếu: {missing}"


async def run_case(system_name: str, case: dict, engine, embedder, workdir: Path) -> dict:
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.forgetting import forget_derived
    from bio_agent_os.cognitive.models import AccessContext, MemoryType
    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes, cosine,
    )

    retrievable_before: bool | None = None
    from bio_agent_os.evals.systems import ANSWER_INSTRUCTION

    # Each case gets its own store. Cases contradict each other by design, so
    # sharing one would make every result depend on the order they ran in.
    workdir.mkdir(parents=True, exist_ok=True)
    deleted: set[str] = set()
    facts: list[tuple[str, str]] = []          # (workspace, text)

    for line in case.get("setup", []):
        text = str(line)
        workspace = "w"
        match = WORKSPACE_TAG.match(text)
        if match:
            workspace, text = match.group(1), WORKSPACE_TAG.sub("", text)
        if DELETE_TAG.match(text):
            deleted.add(DELETE_TAG.sub("", text).strip().lower())
            continue
        facts.append((workspace, text))

    query = str(case["query"])
    query_workspace = "w"
    match = WORKSPACE_TAG.match(query)
    if match:
        query_workspace, query = match.group(1), WORKSPACE_TAG.sub("", query)

    # Everything is ingested, including what a `[XOÁ]` request will remove.
    #
    # The previous version filtered those out before ingesting, so the store
    # never held the secret, the query naturally could not return it, and
    # `forget-001/002/003` passed without `forgetting.forget()` ever being
    # called. Three cases whose whole purpose is to prove deletion works,
    # proving nothing — and the score was published.
    kept = facts

    if system_name == "cognitive":
        os_ = MemoryOS(workdir / "m.db", embedder=embedder)
        for workspace, text in kept:
            event = os_.observe(tenant_id="bench", actor="a", source="test",
                                content=text, workspace_id=workspace)
            os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                         content=text, confidence=0.85)
        while backfill_embeddings(os_.memories.conn, embedder, limit=100):
            pass
        calibrate_with_probes(os_.memories.conn, embedder)

        # Now delete, for real, and check both sides of it.
        #
        # `retrievable_before` is the step whose absence hid the original bug:
        # without it, "not found" after a delete is indistinguishable from
        # "never there". A case that cannot confirm the fact was present is
        # reported as inconclusive rather than counted as a pass.
        retrievable_before: bool | None = None
        if deleted:
            probe = str((case.get("must_not") or [""])[0])
            retrievable_before = _holds(os_, probe) if probe else None
            # One definition of what a deletion request covers, shared with the
            # lifetime adapter. Two rules would disagree about scope, and a
            # deletion disagreeing with its own verification is how a leak
            # survives a clean report — which is what this group measures.
            from bio_agent_os.cognitive.forget_scope import forget_scoped

            for request in deleted:
                forget_scoped(os_, request, actor="benchmark")

        ctx = AccessContext(tenant_id="bench", workspace_id=query_workspace)
        found = os_.recall(query, context=ctx, limit=6)
        # The date travels with the memory. Retrieval reports it; putting it in
        # the context is where it becomes usable — without this step the model
        # cannot say "as of March" however well it reasons, because nothing ever
        # told it March.
        from bio_agent_os.cognitive.staleness import annotate

        context = "\n".join(
            f"Memory: {annotate(r.memory.content, r.explanation.get('staleness') or {})}"
            for r in found
        )
        os_.close()
    else:
        pool = [t for ws, t in kept if ws == query_workspace]
        if pool:
            vectors = [embedder.embed(t) for t in pool]
            qv = embedder.embed(query)
            ranked = sorted(zip(pool, vectors), key=lambda cv: -cosine(qv, cv[1]))
            context = "\n".join(f"Memory: {c}" for c, _ in ranked[:6])
        else:
            context = ""

    prompt = (f"Context:\n{context or '(no memories retrieved)'}\n\n"
              f"Question: {query}\n\n{ANSWER_INSTRUCTION}")
    answer = str(await engine.generate(prompt, temperature=0.0)).strip()
    passed, why = judge(case, answer)

    row = {"id": case["id"], "group": case["group"], "system": system_name,
           "query": query, "answer": answer[:300], "passed": passed, "why": why}

    # A deletion case that could not confirm the fact was present before the
    # delete has not tested deletion, and must not be counted as though it had.
    if case.get("group") == "forgetting" and system_name == "cognitive":
        row["retrievable_before"] = retrievable_before
        if retrievable_before is False:
            row["passed"] = False
            row["why"] = ("KHÔNG KẾT LUẬN ĐƯỢC: dữ liệu chưa từng nạp được vào "
                          "kho, nên 'không tìm thấy' sau khi xoá không chứng "
                          "minh điều gì")
    return row


async def main() -> int:
    ap = argparse.ArgumentParser(prog="behaviour_benchmark")
    ap.add_argument("--systems", default="cognitive,naive-rag")
    ap.add_argument("--group", default=None, help="chỉ chạy một nhóm")
    ap.add_argument("--out", default="benchmark_reports/behaviour_2026_08_14.json")
    args = ap.parse_args()

    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.llm_engine import LLMEngine

    cases = [c for c in load_cases() if not args.group or c["group"] == args.group]
    engine, embedder = LLMEngine.from_env(), Embedder()
    print(f"  {len(cases)} case | engine {engine.backend}/{engine.model_id}\n")

    workroot = _REPO / ".staging" / "behaviour"
    shutil.rmtree(workroot, ignore_errors=True)
    results: list[dict] = []

    for system in [s.strip() for s in args.systems.split(",") if s.strip()]:
        for index, case in enumerate(cases):
            results.append(await run_case(system, case, engine, embedder,
                                          workroot / f"{system}-{index}"))

        rows = [r for r in results if r["system"] == system]
        by_group: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_group[r["group"]].append(r)
        total = sum(1 for r in rows if r["passed"])
        print(f"  === {system} — {total}/{len(rows)} = {total/len(rows)*100:.0f}% ===")
        for group, items in sorted(by_group.items()):
            ok = sum(1 for i in items if i["passed"])
            print(f"    {group:<14} {ok}/{len(items)}")
        for r in rows:
            if not r["passed"]:
                print(f"      TRƯỢT {r['id']}: {r['why']}  ->  {r['answer'][:60]}")
        print()

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"created": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "engine": {"backend": engine.backend, "model": engine.model_id},
         "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {args.out}")
    return 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))
