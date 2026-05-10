from __future__ import annotations

import fnmatch
import html
import json
import time
import uuid
from pathlib import Path

from mitmproxy import http

RULES_PATH = Path(__file__).with_name("rules.json")
HITS_PATH = Path(__file__).with_name("intercepts.jsonl")


def _load_rules() -> list[dict]:
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    rules = data.get("rules", [])
    return rules if isinstance(rules, list) else []


def _matches(rule: dict, host: str, path: str) -> bool:
    if not rule.get("enabled", True):
        return False
    release_until = rule.get("releaseUntilMs")
    if isinstance(release_until, (int, float)) and release_until > int(time.time() * 1000):
        return False
    pattern = str(rule.get("host") or "").lower().strip()
    if not pattern:
        return False
    host_match = fnmatch.fnmatch(host, pattern) or pattern in host
    if not host_match:
        return False
    path_pattern = str(rule.get("path") or "").strip()
    return not path_pattern or fnmatch.fnmatch(path, path_pattern) or path.startswith(path_pattern)


def _write_hit(rule: dict, flow: http.HTTPFlow) -> str:
    hit_id = f"hit-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    entry = {
        "id": hit_id,
        "tsMs": int(time.time() * 1000),
        "ruleId": str(rule.get("id") or ""),
        "host": flow.request.pretty_host,
        "path": flow.request.path,
        "method": flow.request.method,
        "url": flow.request.pretty_url,
        "reason": str(rule.get("reason") or "Atrium intercepted this request."),
    }
    with HITS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return hit_id


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host.lower()
    path = flow.request.path
    for rule in _load_rules():
        if not isinstance(rule, dict) or not _matches(rule, host, path):
            continue
        hit_id = _write_hit(rule, flow)
        reason = str(rule.get("reason") or "Atrium intercepted this request.")
        body = f"""<!doctype html>
<html lang=\"en\">
<head><meta charset=\"utf-8\"><title>Atrium Intercept</title></head>
<body style=\"font-family: Segoe UI, sans-serif; max-width: 680px; margin: 12vh auto; line-height: 1.5; color: #251b17;\">
  <h1 style=\"font-size: 28px;\">Atrium intercepted this page</h1>
  <p>{html.escape(reason)}</p>
  <p style=\"color: #7a5748;\">Rule: {html.escape(str(rule.get('id') or 'unnamed'))}</p>
    <p style=\"color: #7a5748;\">Hit: {html.escape(hit_id)}</p>
</body>
</html>"""
        flow.response = http.Response.make(
            451,
            body.encode("utf-8"),
            {
                "Content-Type": "text/html; charset=utf-8",
                "X-Fiam-Atrium": "intercepted",
                "X-Fiam-Atrium-Rule": str(rule.get("id") or ""),
            },
        )
        return
