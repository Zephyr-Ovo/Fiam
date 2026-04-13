"""
Narrative synthesis — prepare event fragments for background injection.

Two modes (controlled by config.narrative_llm_enabled):
  OFF (default)  — rule-based fragments, no API call, preserves raw texture
  ON             — call configurable LLM to rewrite into prose; cache result

Cache: store/narrative_cache.json  {event_id: {text, model, timestamp}}
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord
from .dynamics import extract_dynamics, parse_body_blocks, relative_time


# ------------------------------------------------------------------
# Fragment extraction (always used — zero LLM cost)
# ------------------------------------------------------------------

def _extract_raw_fragments(event: EventRecord) -> str:
    """High-arousal event: preserve verbatim fragments from body."""
    blocks = parse_body_blocks(event.body)
    if not blocks:
        return event.body[:120] if event.body else ""

    user_blocks = [b for b in blocks if b["role"] == "user"]
    assistant_blocks = [b for b in blocks if b["role"] == "assistant"]

    user_first = ""
    if user_blocks:
        text = user_blocks[0]["content"]
        user_first = text[:80]
        if len(text) > 80:
            user_first += "..."

    core = ""
    if assistant_blocks:
        text = assistant_blocks[0]["content"]
        for end_char in [". ", "! ", "? ", "。", "！", "？"]:
            if end_char in text:
                core = text.split(end_char)[0] + end_char.strip()
                break
        else:
            core = text[:60]
            if len(text) > 60:
                core += "..."

    parts = []
    if user_first:
        parts.append(f'"{user_first}"')
    if core:
        parts.append(f"我：{core}")
    return "\n".join(parts) if parts else ""


def _extract_skeleton(event: EventRecord) -> str:
    """Low-arousal event: rule-based skeleton description."""
    blocks = parse_body_blocks(event.body)
    if not blocks:
        return "那次对话"

    user_blocks = [b for b in blocks if b["role"] == "user"]
    if not user_blocks:
        return "那次对话"

    topic = user_blocks[0]["content"][:15]
    if len(user_blocks[0]["content"]) > 15:
        topic += "..."

    return f"那次关于「{topic}」的对话"


def prepare_materials(events: list[EventRecord]) -> list[dict[str, Any]]:
    """Prepare synthesis materials from a list of events.

    Caps at 5 highest-arousal events.
    """
    if len(events) > 5:
        events = sorted(events, key=lambda e: e.arousal, reverse=True)[:5]
        events.sort(key=lambda e: e.time)

    materials: list[dict[str, Any]] = []
    for event in events:
        if event.arousal > 0.7:
            content = _extract_raw_fragments(event)
        else:
            content = _extract_skeleton(event)

        dynamics = extract_dynamics(event.body)
        when = relative_time(event.time)

        materials.append({
            "event_id": event.event_id,
            "content": content,
            "dynamics": dynamics,
            "when": when,
            "arousal": event.arousal,
        })

    return materials


# ------------------------------------------------------------------
# Rule-based formatting (default mode — no LLM)
# ------------------------------------------------------------------

def format_fragments(materials: list[dict[str, Any]], config: FiamConfig) -> str:
    """Format materials into structured fragments for direct injection.

    The AI itself will integrate these fragments in its own context.
    Zero LLM overhead, preserves raw texture.
    """
    if not materials:
        return ""

    blocks: list[str] = []
    for m in materials:
        block = f"{m['when']}：\n{m['content']}"
        if m["dynamics"]:
            block += f"\n（{m['dynamics']}）"
        blocks.append(block)

    return "\n\n".join(blocks)


# ------------------------------------------------------------------
# LLM rewrite (optional — only when config.narrative_llm_enabled)
# ------------------------------------------------------------------

_SYSTEM_PROMPT_BASE: str | None = None


def _get_system_prompt_base() -> str:
    global _SYSTEM_PROMPT_BASE
    if _SYSTEM_PROMPT_BASE is None:
        from fiam.prompts import load
        _SYSTEM_PROMPT_BASE = load("narrative_recall")
    return _SYSTEM_PROMPT_BASE


def _build_system_prompt(config: FiamConfig) -> str:
    base = _get_system_prompt_base()
    identity_parts = []
    if config.ai_name:
        identity_parts.append(f"你是{config.ai_name}")
    if config.user_name:
        identity_parts.append(f"正在回忆与{config.user_name}的对话")
    if identity_parts:
        return "，".join(identity_parts) + "。\n\n" + base
    return base


def _cache_key(materials: list[dict[str, Any]]) -> str:
    """Deterministic cache key from material event IDs."""
    ids = sorted(m["event_id"] for m in materials)
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]


def _load_cache(config: FiamConfig) -> dict[str, Any]:
    path = config.narrative_cache_path
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(config: FiamConfig, cache: dict[str, Any]) -> None:
    path = config.narrative_cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def synthesize_with_llm(
    materials: list[dict[str, Any]],
    config: FiamConfig,
) -> str:
    """Call LLM to rewrite fragments into prose.  Uses cache.

    Provider support: anthropic (native), openai-compatible (via httpx).
    Falls back to rule-based format_fragments on any failure.
    """
    if not materials:
        return ""

    # Check cache
    key = _cache_key(materials)
    cache = _load_cache(config)
    if key in cache:
        if config.debug_mode:
            print(f"[narrative] Cache hit: {key}")
        return cache[key]["text"]

    # Build prompt
    fragments = format_fragments(materials, config)
    user_prompt = (
        f"这些事忽然想起来了：\n\n{fragments}\n\n"
        "以第一人称写一段回忆。150-250字。英文或中文（根据材料语言自然选择）。\n"
        "像'想起那次...'的感觉，不要列表，不要分段。\n\n"
        "直接写回忆内容，不要任何前缀或解释。"
    )

    try:
        text = _call_llm(config, _build_system_prompt(config), user_prompt)
    except Exception as exc:
        if config.debug_mode:
            print(f"[narrative] LLM call failed ({exc}), using rule-based fallback")
        return format_fragments(materials, config)

    # Write cache
    cache[key] = {
        "text": text,
        "model": config.narrative_llm_model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _save_cache(config, cache)

    return text


def _call_llm(config: FiamConfig, system: str, user: str) -> str:
    """Dispatch to the configured LLM provider."""
    provider = config.narrative_llm_provider.lower()

    if provider == "anthropic":
        return _call_anthropic(config, system, user)
    elif provider == "openai":
        return _call_openai_compatible(config, system, user)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def _call_anthropic(config: FiamConfig, system: str, user: str) -> str:
    import anthropic
    from anthropic.types import TextBlock

    kwargs: dict[str, Any] = {}
    if config.narrative_llm_api_key_env:
        import os
        kwargs["api_key"] = os.environ[config.narrative_llm_api_key_env]

    client = anthropic.Anthropic(**kwargs)
    response = client.messages.create(
        model=config.narrative_llm_model,
        max_tokens=400,
        temperature=0.7,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    block = response.content[0]
    if isinstance(block, TextBlock):
        return block.text.strip()
    return str(block).strip()


def _call_openai_compatible(config: FiamConfig, system: str, user: str) -> str:
    import httpx
    import os

    api_key = ""
    if config.narrative_llm_api_key_env:
        api_key = os.environ.get(config.narrative_llm_api_key_env, "")

    base_url = config.narrative_llm_base_url.rstrip("/")
    if not base_url:
        raise ValueError("narrative_llm_base_url required for openai provider")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": config.narrative_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 400,
        "temperature": 0.7,
    }

    resp = httpx.post(
        f"{base_url}/chat/completions",
        json=payload,
        headers=headers,
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def synthesize_narrative(materials: list[dict[str, Any]], config: FiamConfig) -> str:
    """Generate narrative text from materials.

    Dispatches to LLM or rule-based path based on config.
    """
    if not materials:
        return ""

    if config.narrative_llm_enabled:
        return synthesize_with_llm(materials, config)
    else:
        return format_fragments(materials, config)
