"""Favilla app marker parsing helpers."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from fiam.config import FiamConfig

# COT markers: <cot>...</cot> for shareable thought blocks; <lock/> to lock
# the entire turn's thought chain (covers marker thoughts + any native
# reasoning the runtime carries).
_COT_BLOCK_RE = re.compile(r"<cot>\s*(.*?)\s*</cot>", re.DOTALL | re.IGNORECASE)
_VOICE_BLOCK_RE = re.compile(r"<voice>\s*(.*?)\s*</voice>", re.DOTALL | re.IGNORECASE)
_COT_OR_VOICE_RE = re.compile(r"<(cot|voice)>\s*(.*?)\s*</\1>", re.DOTALL | re.IGNORECASE)
_LOCK_RE = re.compile(r"<lock\s*/>", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _mask_code_spans(text: str) -> tuple[str, dict[str, str]]:
    """Hide fenced/inline code spans behind placeholders so cot/lock regexes
    don't trip over `<cot>` literals appearing inside code samples."""
    placeholders: dict[str, str] = {}
    counter = [0]

    def _replace(m: "re.Match[str]") -> str:
        counter[0] += 1
        key = f"\x00CODE{counter[0]}\x00"
        placeholders[key] = m.group(0)
        return key

    masked = _CODE_FENCE_RE.sub(_replace, text)
    masked = _INLINE_CODE_RE.sub(_replace, masked)
    return masked, placeholders


def _unmask_code_spans(text: str, placeholders: dict[str, str]) -> str:
    if not placeholders:
        return text
    for key, original in placeholders.items():
        text = text.replace(key, original)
    return text


def split_cot_segments(chunk: str) -> list[tuple[str, str]]:
    """Split a text chunk into ordered ('text'|'thought'|'voice', body) segments,
    skipping tags that appear inside markdown code spans."""
    if not chunk:
        return []
    masked, placeholders = _mask_code_spans(chunk)
    segs: list[tuple[str, str]] = []
    cursor = 0
    for m in _COT_OR_VOICE_RE.finditer(masked):
        before = masked[cursor:m.start()]
        if before.strip():
            segs.append(("text", _unmask_code_spans(before, placeholders)))
        tag = m.group(1).lower()
        kind = "thought" if tag == "cot" else "voice"
        body = (m.group(2) or "").strip()
        if body:
            segs.append((kind, _unmask_code_spans(body, placeholders)))
        cursor = m.end()
    tail = masked[cursor:]
    if tail.strip():
        segs.append(("text", _unmask_code_spans(tail, placeholders)))
    return segs


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

    locked = bool(_LOCK_RE.search(_mask_code_spans(reply)[0]))
    segments: list[dict[str, Any]] = []
    thoughts_raw: list[dict[str, Any]] = []
    for kind, body in split_cot_segments(reply):
        if kind == "text":
            cleaned = _strip_cot_control(body).strip()
            if cleaned:
                segments.append({"type": "text", "text": cleaned})
        elif kind == "voice":
            segments.append({"type": "voice", "text": body})
        else:
            step = {"kind": "think", "text": body, "source": "fiam"}
            thoughts_raw.append(step)
            segments.append({"type": "thought", **step})

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
        segment["icon"] = "Sparkles"
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
        item["icon"] = "Sparkles"
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

    base_url = (getattr(config, "app_cot_summary_base_url", "") or "https://token-plan-cn.xiaomimimo.com/v1").rstrip("/")
    model = getattr(config, "app_cot_summary_model", "") or "mimo-v2-omni"
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
        "max_tokens": 800,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
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
    api_key = os.environ.get("FIAM_MIMO_API_KEY", "").strip() or os.environ.get("MIMO_API_KEY", "").strip()
    if api_key:
        return api_key, "FIAM_MIMO_API_KEY"
    api_key = os.environ.get("FIAM_GRAPH_API_KEY", "").strip()
    if api_key:
        return api_key, "FIAM_GRAPH_API_KEY"
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


def narrate_recall_fragments(
    fragments: list[dict[str, Any]],
    config: FiamConfig | None,
) -> str | None:
    """Send recall fragments through ds for AI-voice narration.

    fragments: [{"hint": str, "text": str}, ...]  (hint = "刚才"/"3天前"/...; text = raw event body)

    Returns markdown string (one bullet per fragment) or None if ds is unavailable.
    The narration is in the AI's first-person voice ('我'), addressing the user as
    '你', and must NOT add anything that isn't in the fragments.
    """
    if not fragments or config is None:
        return None
    if not getattr(config, "app_cot_summary_enabled", True):
        return None
    api_key, _ = _summary_api_key(config)
    if not api_key:
        return None

    base_url = (getattr(config, "app_cot_summary_base_url", "") or "https://token-plan-cn.xiaomimimo.com/v1").rstrip("/")
    model = getattr(config, "app_cot_summary_model", "") or "mimo-v2-omni"
    items = [
        {"index": i, "hint": str(f.get("hint") or ""), "text": str(f.get("text") or "")[:1800]}
        for i, f in enumerate(fragments)
    ]
    system = (
        "You are the AI assistant's memory module. Recalled past events come in as raw "
        "fragments; you narrate them back to the AI in the AI's first-person voice. "
        "Use '我' for the AI and '你' for the user. Distinguish who said/did what. "
        "Describe — don't summarize, don't compress hard. Don't add anything that "
        "isn't in the fragment. Don't editorialize. Keep the time hint at the front. "
        "Output ONLY a JSON array of objects: {index, line}, where line is one markdown "
        "bullet starting with '- (time-hint) ' followed by your narration. Match the "
        "input language; default to Chinese."
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
        ],
        "temperature": 0.4,
        "max_tokens": 4000,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        parsed = _parse_summary_json(content)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, IndexError, KeyError, TypeError, ValueError):
        return None

    by_idx: dict[int, str] = {}
    for item in parsed:
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        line = str(item.get("line") or "").strip()
        if line:
            by_idx[idx] = line
    if not by_idx:
        return None
    out_lines = []
    for i in range(len(fragments)):
        if i in by_idx:
            out_lines.append(by_idx[i])
        else:
            f = fragments[i]
            out_lines.append(f"- ({f.get('hint','')}) {f.get('text','')}")
    return "\n".join(out_lines)


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
    return "Sparkles"


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
