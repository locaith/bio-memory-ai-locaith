from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


OPENCLAW_NODE = os.getenv("OPENCLAW_NODE", "node")
OPENCLAW_CLI_PATH = Path(os.getenv("OPENCLAW_CLI_PATH", r"D:\Openclaw\dist\index.js"))
BIO_AGENT_API_BASE_URL = os.getenv("BIO_AGENT_API_BASE_URL", "http://127.0.0.1:8055")
REPORT_DIR = Path("benchmark_reports")


def http_json(method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BIO_AGENT_API_BASE_URL}{path}",
        data=body,
        headers={"content-type": "application/json", "accept": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_openclaw_json(stdout: str) -> Dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("OpenClaw returned empty output.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def run_openclaw_turn(session_id: str, message: str) -> Dict[str, Any]:
    command = [
        OPENCLAW_NODE,
        str(OPENCLAW_CLI_PATH),
        "agent",
        "--session-id",
        session_id,
        "--message",
        message,
        "--json",
    ]
    started = time.perf_counter()
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = round(time.perf_counter() - started, 3)
    if result.returncode != 0:
        raise RuntimeError(f"OpenClaw failed ({result.returncode}).\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    payload = parse_openclaw_json(result.stdout)
    payload["_latency_s"] = elapsed
    return payload


def wait_for_exact_resolution(subject: str, expected_value: str, task_id: str, timeout_s: float = 18.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    query = f"What is the {subject}?"
    while time.time() < deadline:
        retrieval = http_json(
            "POST",
            "/api/retrieve",
            {
                "query": query,
                "task_id": task_id,
                "workspace_id": "main",
                "project_version": "v1",
                "mode": "implement",
            },
        )
        exact = retrieval.get("exact_facts", {})
        if exact.get("status") == "resolved" and str(exact.get("answer_candidate", "")).lower() == expected_value.lower():
            return retrieval
        time.sleep(1.2)
    return retrieval


def extract_response_text(payload: Dict[str, Any]) -> str:
    result = payload.get("result", payload)
    if isinstance(result, dict):
        for key in ("text", "assistantText", "outputText", "response"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        payloads = result.get("payloads")
        if isinstance(payloads, list):
            for item in payloads:
                text = item.get("text") if isinstance(item, dict) else None
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return ""


@dataclass
class HopResult:
    source_channel: str
    target_channel: str
    subject: str
    expected_value: str
    response: str
    exact_status: str
    success: bool
    strict_success: bool
    asserted_value: str
    latency_s: float


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("*", "").replace("`", "").split()).strip().lower()


def extract_asserted_value(response: str, subject: str) -> str:
    cleaned = " ".join(str(response or "").replace("*", "").replace("`", "").split()).strip()
    if not cleaned:
        return ""
    patterns = [
        rf"{re.escape(subject)}\s+is\s+(?P<value>[^.\n]+)",
        r"(?:the\s+)?special character(?:\s+for\s+[^.\n]+)?\s+is\s+(?P<value>[^.\n]+)",
        r"(?:the\s+)?secret code(?:\s+for\s+[^.\n]+)?\s+is\s+(?P<value>[^.\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group("value")).strip(" .,:;!?-")
        if value:
            return value
    return ""


def judge_strict_success(response: str, subject: str, expected_value: str) -> tuple[bool, str]:
    asserted_value = extract_asserted_value(response, subject)
    if not asserted_value:
        return False, ""
    return normalize_text(asserted_value) == normalize_text(expected_value), asserted_value


def teach_via_api(channel: str, subject: str, value: str, task_id: str):
    http_json(
        "POST",
        "/api/ingest",
        {
            "text": f"The {subject} is {value}.",
            "source": f"{channel}-user",
            "task_id": task_id,
            "workspace_id": "main",
            "project_version": "v1",
            "observation_type": "chat_input",
            "source_refs": [{"role": "user", "channel": channel, "kind": "benchmark"}],
        },
    )


def query_via_api(channel: str, subject: str, expected_value: str, task_id: str) -> HopResult:
    query = f"What is the {subject}?"
    retrieval = http_json(
        "POST",
        "/api/retrieve",
        {
            "query": query,
            "task_id": task_id,
            "workspace_id": "main",
            "project_version": "v1",
            "mode": "implement",
        },
    )
    started = time.perf_counter()
    chat = http_json(
        "POST",
        "/api/chat",
        {
            "message": query,
            "task_id": task_id,
            "workspace_id": "main",
            "project_version": "v1",
            "source_refs": [{"role": "user", "channel": channel, "kind": "benchmark"}],
        },
    )
    latency = round(time.perf_counter() - started, 3)
    response = str(chat.get("response", ""))
    strict_success, asserted_value = judge_strict_success(response, subject, expected_value)
    success = expected_value.lower() in response.lower()
    return HopResult(
        source_channel="api",
        target_channel=channel,
        subject=subject,
        expected_value=expected_value,
        response=response,
        exact_status=str(retrieval.get("exact_facts", {}).get("status", "none")),
        success=success,
        strict_success=strict_success,
        asserted_value=asserted_value,
        latency_s=latency,
    )


def query_via_openclaw(session_id: str, subject: str, expected_value: str) -> HopResult:
    payload = run_openclaw_turn(session_id, f"What is the {subject}?")
    response = extract_response_text(payload)
    if "couldn't generate a response" in response.lower():
        time.sleep(2.0)
        payload = run_openclaw_turn(session_id, f"What is the {subject}?")
        response = extract_response_text(payload)
    retrieval = http_json(
        "POST",
        "/api/retrieve",
        {
            "query": f"What is the {subject}?",
            "task_id": session_id,
            "workspace_id": "main",
            "project_version": "v1",
            "mode": "implement",
        },
    )
    strict_success, asserted_value = judge_strict_success(response, subject, expected_value)
    return HopResult(
        source_channel="api",
        target_channel="openclaw",
        subject=subject,
        expected_value=expected_value,
        response=response,
        exact_status=str(retrieval.get("exact_facts", {}).get("status", "none")),
        success=expected_value.lower() in response.lower(),
        strict_success=strict_success,
        asserted_value=asserted_value,
        latency_s=float(payload.get("_latency_s", 0.0)),
    )


def teach_via_openclaw(session_id: str, subject: str, value: str):
    run_openclaw_turn(session_id, f"Remember exactly: the {subject} is {value}.")


def ensure_prerequisites():
    if not OPENCLAW_CLI_PATH.exists():
        raise FileNotFoundError(f"OpenClaw CLI not found at {OPENCLAW_CLI_PATH}")
    http_json("GET", "/api/status")


def scenario_web_to_telegram_and_openclaw(run_token: str) -> List[HopResult]:
    subject = f"special character for cross channel web {run_token}"
    expected = "Milky Way"
    teach_via_api("web", subject, expected, task_id=f"web-{run_token}")
    wait_for_exact_resolution(subject, expected, task_id=f"web-{run_token}")
    results = [
        query_via_api("telegram", subject, expected, task_id=f"telegram-{run_token}"),
        query_via_openclaw(f"cross-web-{run_token}", subject, expected),
    ]
    return results


def scenario_telegram_to_web_and_openclaw(run_token: str) -> List[HopResult]:
    subject = f"special character for cross channel telegram {run_token}"
    expected = "Orion Bloom"
    teach_via_api("telegram", subject, expected, task_id=f"telegram-src-{run_token}")
    wait_for_exact_resolution(subject, expected, task_id=f"telegram-src-{run_token}")
    results = [
        query_via_api("web", subject, expected, task_id=f"web-dst-{run_token}"),
        query_via_openclaw(f"cross-telegram-{run_token}", subject, expected),
    ]
    return results


def scenario_openclaw_to_web_and_telegram(run_token: str) -> List[HopResult]:
    subject = f"special character for cross channel openclaw {run_token}"
    expected = "Solar Tide"
    session_id = f"cross-openclaw-{run_token}"
    teach_via_openclaw(session_id, subject, expected)
    wait_for_exact_resolution(subject, expected, task_id=session_id)
    results = [
        query_via_api("web", subject, expected, task_id=f"web-openclaw-{run_token}"),
        query_via_api("telegram", subject, expected, task_id=f"telegram-openclaw-{run_token}"),
    ]
    return results


def write_reports(metrics: Dict[str, Any]):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "cross_channel_consistency_benchmark.json"
    md_path = REPORT_DIR / "cross_channel_consistency_benchmark.md"
    json_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Cross-Channel Consistency Benchmark",
        "",
        f"- Run token: `{metrics['run_token']}`",
        f"- Consistency rate: `{metrics['channel_consistency_rate']:.3f}`",
        f"- Exact resolution rate: `{metrics['exact_resolution_rate']:.3f}`",
        f"- OpenClaw hop rate: `{metrics['openclaw_consistency_rate']:.3f}`",
        "",
        "## Hops",
    ]
    for hop in metrics["hops"]:
        lines.extend(
            [
                f"- `{hop['target_channel']}` on `{hop['subject']}` -> mention=`{hop['success']}` strict=`{hop['strict_success']}` exact=`{hop['exact_status']}` latency=`{hop['latency_s']}`",
                f"  asserted: {hop.get('asserted_value') or '(none)'}",
                f"  response: {hop['response']}",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if hasattr(os.sys.stdout, "reconfigure"):
        os.sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ensure_prerequisites()
    run_token = time.strftime("%Y%m%d%H%M%S")
    hops = (
        scenario_web_to_telegram_and_openclaw(run_token)
        + scenario_telegram_to_web_and_openclaw(run_token)
        + scenario_openclaw_to_web_and_telegram(run_token)
    )
    consistency_rate = sum(1 for hop in hops if hop.strict_success) / max(len(hops), 1)
    exact_resolution_rate = sum(1 for hop in hops if hop.exact_status == "resolved") / max(len(hops), 1)
    openclaw_hops = [hop for hop in hops if hop.target_channel == "openclaw"]
    openclaw_consistency_rate = sum(1 for hop in openclaw_hops if hop.strict_success) / max(len(openclaw_hops), 1)
    metrics = {
        "run_token": run_token,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "channel_consistency_rate": round(consistency_rate, 3),
        "exact_resolution_rate": round(exact_resolution_rate, 3),
        "openclaw_consistency_rate": round(openclaw_consistency_rate, 3),
        "hops": [hop.__dict__ for hop in hops],
    }
    write_reports(metrics)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
