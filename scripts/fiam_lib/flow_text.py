"""Helpers for presenting flow beats with speaker labels in text."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fiam_lib.jsonl import _claude_projects_dir, _sanitize_home_path

_SPEAKER_RE = re.compile(r"^[^\s:：\[\](){}<>]{1,32}[：:]")
_SYSTEM_TAG_RE = re.compile(
    r"^<(?:teammate-message|local-command-caveat|local-command-stdout|"
    r"command-name|command-message|command-args|task-notification|"
    r"system-reminder|user-prompt-submit-hook)[>\s/]",
    re.IGNORECASE,
)

_CC_ROLE_CACHE: dict[str, Any] = {"snapshot": None, "lookup": {}}


def normalize_beats(beats: list[dict], *, config: Any, root: Path | None = None) -> list[dict]:
    lookup = _cc_role_lookup(config)
    return [normalize_beat(beat, config=config, cc_lookup=lookup) for beat in beats]


def normalize_beat(beat: dict, *, config: Any, cc_lookup: dict[str, str] | None = None) -> dict:
    text = str(beat.get("text") or "").strip()
    if not text or _has_speaker(text):
        return beat

    speaker = _speaker_for_beat(beat, config=config, cc_lookup=cc_lookup or {})
    if not speaker:
        return beat
    out = dict(beat)
    out["text"] = f"{speaker}：{text}"
    return out


def _speaker_for_beat(beat: dict, *, config: Any, cc_lookup: dict[str, str]) -> str:
    text = str(beat.get("text") or "").strip()
    # Accept both new "scene" and legacy "source" fields. Strip the actor
    # prefix so legacy "user@favilla" / new bare "favilla" both resolve.
    raw_scene = str(beat.get("scene") or beat.get("source") or "").strip().lower()
    scene_tail = raw_scene.split("@", 1)[-1] if "@" in raw_scene else raw_scene
    actor = raw_scene.split("@", 1)[0] if "@" in raw_scene else ""
    user_label = _label(getattr(config, "user_name", ""), "zephyr")
    ai_label = _label(getattr(config, "ai_name", ""), "ai")

    if scene_tail == "action" or actor == "ai" and scene_tail == "action":
        return ""
    if actor == "ai":
        return ai_label
    if actor == "user":
        return user_label
    if actor == "external":
        return scene_tail or "external"
    if actor == "system":
        return scene_tail or "system"
    # Legacy bare values
    if raw_scene in {"dispatch", "todo", "limen", "xiao", "ring"}:
        return ai_label if raw_scene == "dispatch" else raw_scene
    if raw_scene in {"favilla", "app", "webapp", "stroll"}:
        return user_label
    if raw_scene == "email":
        return raw_scene
    if raw_scene == "cc":
        if text.startswith("[app:"):
            return user_label
        return cc_lookup.get(text) or "cc"
    return raw_scene or user_label


def _cc_role_lookup(config: Any) -> dict[str, str]:
    jsonl_dir = _claude_projects_dir() / _sanitize_home_path(config.home_path)
    if not jsonl_dir.is_dir():
        return {}
    try:
        files = sorted(jsonl_dir.glob("*.jsonl"))
        snapshot = tuple((path.name, path.stat().st_size, path.stat().st_mtime_ns) for path in files)
    except OSError:
        return {}

    if _CC_ROLE_CACHE.get("snapshot") == snapshot:
        return dict(_CC_ROLE_CACHE.get("lookup") or {})

    user_label = _label(getattr(config, "user_name", ""), "zephyr")
    ai_label = _label(getattr(config, "ai_name", ""), "ai")
    lookup: dict[str, str] = {}

    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            line_type = entry.get("type")
            content = message.get("content", "")
            if line_type == "user" and isinstance(content, str):
                text = content.strip()
                if text and not _SYSTEM_TAG_RE.match(text):
                    lookup.setdefault(text, user_label)
            elif line_type == "assistant" and isinstance(content, list):
                text_parts = [
                    block.get("text", "").strip()
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text = "\n".join(part for part in text_parts if part)
                if text:
                    lookup.setdefault(text, ai_label)
                thinking_parts = [
                    block.get("thinking", "").strip()
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "thinking"
                ]
                thinking = "\n".join(part for part in thinking_parts if part)
                if thinking:
                    lookup.setdefault(f"我想：{thinking}", ai_label)
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        brief = _tool_brief(str(block.get("name") or "unknown"), block.get("input") or {})
                        lookup.setdefault(brief, ai_label)

    _CC_ROLE_CACHE["snapshot"] = snapshot
    _CC_ROLE_CACHE["lookup"] = lookup
    return dict(lookup)


def _tool_brief(name: str, payload: dict) -> str:
    if name in ("Read", "Glob"):
        path = payload.get("path") or payload.get("pattern", "")
        return f"[{name}] {path}" if path else f"[{name}]"
    if name == "Write":
        path = payload.get("path", "")
        return f"[Write] {path}" if path else "[Write]"
    if name == "Edit":
        path = payload.get("path") or payload.get("file_path", "")
        return f"[Edit] {path}" if path else "[Edit]"
    if name == "Bash":
        command = str(payload.get("command", ""))
        if len(command) > 80:
            command = command[:77] + "..."
        return f"[Bash] {command}" if command else "[Bash]"
    return f"[{name}]"


def _has_speaker(text: str) -> bool:
    first_line = text.splitlines()[0].strip() if text else ""
    return bool(_SPEAKER_RE.match(first_line))


def _label(value: Any, fallback: str) -> str:
    label = str(value or fallback).strip().lower()
    return label or fallback