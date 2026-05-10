"""Favilla app marker parsing helpers."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fiam.config import FiamConfig
from fiam.markers import parse_hold_kind, strip_xml_markers

# COT markers: <cot>...</cot> for shareable thought blocks; <lock/> to lock
# the entire turn's thought chain (covers marker thoughts + any native
# reasoning the runtime carries).
_COT_BLOCK_RE = re.compile(r"<cot>\s*(.*?)\s*</cot>", re.DOTALL | re.IGNORECASE)
_LOCK_RE = re.compile(r"<lock\s*/>", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AppCotResult:
    reply: str
    thoughts: list[dict[str, Any]]
    locked: bool
    segments: list[dict[str, Any]]


def parse_app_cot(reply: str, config: FiamConfig | None = None) -> AppCotResult:
    """Strip COT markers while preserving thought/text order for Favilla."""
    if not reply:
        return AppCotResult("", [], False, [])

    locked = bool(_LOCK_RE.search(reply))
    segments: list[dict[str, Any]] = []
    thoughts_raw: list[dict[str, Any]] = []
    cursor = 0
    for match in _COT_BLOCK_RE.finditer(reply):
        before = _strip_cot_control(reply[cursor:match.start()]).strip()
        if before:
            segments.append({"type": "text", "text": before})
        body = (match.group(1) or "").strip()
        if body:
            step = {"kind": "think", "text": body, "source": "marker"}
            thoughts_raw.append(step)
            segments.append({"type": "thought", **step})
        cursor = match.end()

    tail = _strip_cot_control(reply[cursor:]).strip()
    if tail:
        segments.append({"type": "text", "text": tail})

    text_segments = [s for s in segments if s.get("type") == "text" and str(s.get("text") or "").strip()]
    thought_segments = [s for s in segments if s.get("type") == "thought"]
    if not text_segments and thought_segments and not locked:
        last = thought_segments[-1]
        text = str(last.get("text") or "").strip()
        segments = [s for s in segments if s is not last]
        if text:
            segments.append({"type": "text", "text": text})
        thoughts_raw = [t for t in thoughts_raw if str(t.get("text") or "").strip() != text]

    summaries = summarize_cot_steps(thoughts_raw, locked=locked, config=config)
    summary_by_index = {item["index"]: item for item in summaries}
    thought_index = 0
    for segment in segments:
        if segment.get("type") != "thought":
            continue
        summary = summary_by_index.get(thought_index, {})
        segment["summary"] = summary.get("summary") or _fallback_summary(str(segment.get("text") or ""), locked)
        segment["icon"] = summary.get("icon") or _fallback_icon(str(segment.get("text") or ""), locked)
        segment["locked"] = locked
        if locked:
            segment.pop("text", None)
        thought_index += 1

    thoughts: list[dict[str, Any]] = []
    thought_index = 0
    for raw in thoughts_raw:
        summary = summary_by_index.get(thought_index, {})
        item = dict(raw)
        item["summary"] = summary.get("summary") or _fallback_summary(str(raw.get("text") or ""), locked)
        item["icon"] = summary.get("icon") or _fallback_icon(str(raw.get("text") or ""), locked)
        item["locked"] = locked
        if locked:
            item["text"] = item["summary"]
        thoughts.append(item)
        thought_index += 1

    visible_reply = "\n\n".join(
        str(segment.get("text") or "").strip()
        for segment in segments
        if segment.get("type") == "text" and str(segment.get("text") or "").strip()
    )
    return AppCotResult(visible_reply, thoughts, locked, segments)


def strip_hold_markers(text: str) -> str:
    return strip_xml_markers(text or "", {"hold"}).strip()


def apply_hold(
    text: str,
    config: FiamConfig,
    *,
    channel: str,
    runtime: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Apply ``<hold/>`` / ``<hold all/>`` filtering and queue a retry todo.

    Returns ``(cleaned_text, kind, retry_todos)`` where ``kind`` is ``""``,
    ``"text"``, or ``"all"``. When a hold is detected, a single retry todo is
    scheduled at ``now + config.hold_retry_seconds`` so the AI can take
    another pass; the original output stays in transcripts/context so the AI
    can see what it just held.
    """
    kind = parse_hold_kind(text or "")
    cleaned = strip_hold_markers(text or "")
    if not kind:
        return cleaned, "", []
    delay = max(1, int(getattr(config, "hold_retry_seconds", 30) or 30))
    retry_at = config.now_local() + timedelta(seconds=delay)
    todo = {
        "at": retry_at.isoformat(),
        "type": "private",
        "action": "hold_retry",
        "channel": channel,
        "runtime": runtime,
        "reason": f"hold {kind} retry",
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return cleaned, kind, [todo]


def _strip_cot_control(text: str) -> str:
    return _LOCK_RE.sub("", text)


def summarize_cot_steps(steps: list[dict[str, Any]], *, locked: bool, config: FiamConfig | None) -> list[dict[str, Any]]:
    if not steps:
        return []
    fallback = [
        {"index": i, "summary": _fallback_summary(str(step.get("text") or ""), locked), "icon": _fallback_icon(str(step.get("text") or ""), locked)}
        for i, step in enumerate(steps)
    ]
    if config is None or not getattr(config, "app_cot_summary_enabled", True):
        return fallback
    api_key, _api_key_env = _summary_api_key(config)
    if not api_key:
        return fallback

    base_url = (getattr(config, "app_cot_summary_base_url", "") or "https://api.deepseek.com").rstrip("/")
    model = getattr(config, "app_cot_summary_model", "") or "deepseek-chat"
    prompt = {
        "locked": locked,
        "items": [{"index": i, "text": str(step.get("text") or "")[:1800]} for i, step in enumerate(steps)],
    }
    system = (
        "You write tiny English UI state labels for a chat thought chain. "
        "Return only a JSON array of objects: {index, summary, icon}. "
        "summary should be casual, emotional when relevant, 2-7 words, not formal, and need not cover every detail. "
        "icon must be a lucide-react PascalCase icon component name. "
        "For locked=true, do not reveal names, facts, files, plans, conclusions, or specific content from the text; only capture mood or process."
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.35,
        "max_tokens": 220,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        parsed = _parse_summary_json(content)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError, KeyError, TypeError, ValueError):
        return fallback
    merged = {item["index"]: item for item in fallback}
    for item in parsed:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(steps):
            continue
        summary = _clean_summary(str(item.get("summary") or ""))
        icon = _clean_icon(str(item.get("icon") or ""))
        if summary:
            merged[idx]["summary"] = summary
        if icon:
            merged[idx]["icon"] = icon
    return [merged[i] for i in range(len(steps))]


def _summary_api_key(config: FiamConfig) -> tuple[str, str]:
    env_name = getattr(config, "app_cot_summary_api_key_env", "") or "FIAM_COT_SUMMARY_API_KEY"
    api_key = os.environ.get(env_name, "").strip()
    if api_key:
        return api_key, env_name
    fallback_env = getattr(config, "graph_edge_api_key_env", "") or ""
    if fallback_env and fallback_env != env_name:
        api_key = os.environ.get(fallback_env, "").strip()
        if api_key:
            return api_key, fallback_env
    return "", env_name


def _parse_summary_json(content: str) -> list[dict[str, Any]]:
    clean = content.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    if not clean.startswith("["):
        match = re.search(r"\[[\s\S]*\]", clean)
        if match:
            clean = match.group(0)
    data = json.loads(clean)
    return data if isinstance(data, list) else []


def _fallback_summary(text: str, locked: bool) -> str:
    if locked:
        return "thinking privately"
    line = " ".join(text.strip().split())
    if not line:
        return "thinking it through"
    if len(line) <= 54:
        return line
    return line[:51].rstrip() + "..."


def _fallback_icon(text: str, locked: bool) -> str:
    if locked:
        return "Lock"
    lowered = text.lower()
    if any(word in lowered for word in ("search", "look up", "查", "找")):
        return "Search"
    if any(word in lowered for word in ("check", "verify", "确认", "检查")):
        return "CheckCircle2"
    return "Brain"


def _clean_summary(value: str) -> str:
    clean = " ".join(value.strip().split())
    if not clean:
        return ""
    return clean[:80]


def _clean_icon(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]", "", value.strip())
    if not clean:
        return ""
    return clean[:40]