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
    """High-intensity event: preserve verbatim fragments from body."""
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
    """Low-intensity event: rule-based skeleton description."""
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


def prepare_materials(
    events: list[EventRecord],
    edges: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Prepare synthesis materials from a list of events.

    If *edges* are given (from GraphStore), relationships between events
    are included in the materials for edge-aware narrative synthesis.
    """
    if len(events) > 5:
        events = sorted(events, key=lambda e: e.intensity, reverse=True)[:5]
        events.sort(key=lambda e: e.time)

    # Build event ID set for edge filtering
    event_ids = {e.event_id for e in events}

    materials: list[dict[str, Any]] = []
    for event in events:
        if event.intensity > 0.7:
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
            "intensity": event.intensity,
        })

    # Attach relationship edges between recalled events
    if edges:
        relevant = [
            e for e in edges
            if e.src in event_ids and e.dst in event_ids
            and e.type not in ("temporal",)  # skip temporal — just adjacency
        ]
        for m in materials:
            rels: list[str] = []
            for e in relevant:
                if e.src == m["event_id"]:
                    label = _edge_label(e.type, e.dst, e.reason)
                    if label:
                        rels.append(label)
                elif e.dst == m["event_id"]:
                    label = _edge_label_reverse(e.type, e.src, e.reason)
                    if label:
                        rels.append(label)
            if rels:
                m["relationships"] = rels

    return materials


def _edge_label(edge_type: str, target_id: str, reason: str = "") -> str:
    """Human-readable edge label from this event TO target."""
    name = target_id.replace("_", " ")
    labels = {
        "cause": f"导致了「{name}」",
        "remind": f"和「{name}」有关联",
        "elaboration": f"是「{name}」的展开",
        "contrast": f"和「{name}」形成对比",
        "semantic": f"和「{name}」话题相近",
    }
    base = labels.get(edge_type, f"→ {name}")
    if reason:
        base += f"（{reason}）"
    return base


def _edge_label_reverse(edge_type: str, source_id: str, reason: str = "") -> str:
    """Human-readable edge label from source TO this event."""
    name = source_id.replace("_", " ")
    labels = {
        "cause": f"由「{name}」引起",
        "remind": f"和「{name}」有关联",
        "elaboration": f"是对「{name}」的展开",
        "contrast": f"和「{name}」形成对比",
        "semantic": f"和「{name}」话题相近",
    }
    base = labels.get(edge_type, f"← {name}")
    if reason:
        base += f"（{reason}）"
    return base


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
        rels = m.get("relationships")
        if rels:
            block += "\n关联：" + "；".join(rels)
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
        f"这些记忆浮上来了：\n\n{fragments}\n\n"
        "用第一人称把这些记忆梳理出来。\n"
        "注意：内容中的「（略）」表示省略的代码块或大段粘贴内容（不是真的说了'略'），可根据AI回复推断被省略的内容大意。\n"
        "按关联关系来组织叙述结构（因果、展开、对比等），不要逐条罗列事件。\n"
        "不要概括或总结，保留原始细节和引用。\n"
        "像自然地回忆，150-300字。\n\n"
        "直接写内容，不要前缀。"
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
    elif provider in ("openai", "deepseek"):
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
    # Cache the system prompt — it's identity + instruction text, stable across calls.
    # Anthropic charges ~10% of write cost on cache hits and (on subscription tiers)
    # cache hits don't count against rate limits. Requires the system field to be a
    # list of content blocks with a cache_control marker.
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
    ]
    response = client.messages.create(
        model=config.narrative_llm_model,
        max_tokens=400,
        temperature=0.7,
        system=system_blocks,  # type: ignore[arg-type]
        messages=[{"role": "user", "content": user}],
    )
    if config.debug_mode:
        usage = getattr(response, "usage", None)
        if usage is not None:
            cc_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cc_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            print(f"[narrative] anthropic cache: read={cc_read} write={cc_write}")
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
