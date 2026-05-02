"""Prompt assembly for API-backed fiam runtimes.

Layout:
    [system 1] constitution.md       (cache_control if non-empty)
    [system 2] self/*.md combined    (cache_control if non-empty)
    [system 3] [recall] + extras     (no cache, churn-prone)
    [user]    [wake:source] text

cache_control uses the OpenAI-compatible structured-blocks form. Providers
that don't support prefix caching (e.g. DeepSeek, Gemini) silently ignore
the field per OpenRouter's pass-through spec; Anthropic-family models honor
it (5-minute ephemeral TTL, byte-identical prefix required).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fiam.config import FiamConfig


def _system_block(text: str, *, cache: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return {"role": "system", "content": [block]}


def build_api_messages(
    config: "FiamConfig",
    user_text: str,
    *,
    source: str = "api",
    include_recall: bool = True,
    consume_recall_dirty: bool = True,
    extra_context: str = "",
) -> list[dict[str, Any]]:
    """Build OpenAI-compatible messages with three system segments.

    Segments are emitted only when non-empty so models don't see blank blocks.
    """
    messages: list[dict[str, Any]] = []

    # system 1 — constitution.md (project knowledge / environment / guide)
    constitution = _read_text(config.constitution_md_path).strip()
    if not constitution:
        # Backward compat: fall back to legacy CLAUDE.md if constitution.md absent.
        constitution = _read_text(config.claude_md_path).strip()
    if constitution:
        messages.append(_system_block(constitution, cache=True))

    # system 2 — self/*.md combined (identity / impressions / lessons / commitments)
    self_context = load_self_context(config)
    if self_context:
        messages.append(_system_block(self_context, cache=True))

    # system 3 — recall + extras (churn-prone, no cache)
    dynamic_parts: list[str] = []
    if include_recall:
        recall = load_recall_context(config, consume_dirty=consume_recall_dirty)
        if recall:
            dynamic_parts.append(f"[recall]\n{recall}")
    if extra_context.strip():
        dynamic_parts.append(extra_context.strip())
    if dynamic_parts:
        messages.append(_system_block("\n\n".join(dynamic_parts), cache=False))

    messages.append(
        {"role": "user", "content": f"[wake:{source}] {user_text.strip()}"}
    )
    return messages


def load_self_context(config: "FiamConfig") -> str:
    """Load self/*.md in the same shape as the CC inject hook."""
    self_dir = config.self_dir
    if not self_dir.is_dir():
        return ""
    parts: list[str] = []
    for path in sorted(self_dir.glob("*.md")):
        content = _read_text(path).strip()
        if content:
            parts.append(f"# {path.stem}\n{content}")
    return "\n".join(parts)


def load_recall_context(config: "FiamConfig", *, consume_dirty: bool = False) -> str:
    """Load recall.md only when .recall_dirty exists, matching hook semantics."""
    dirty = config.background_path.parent / ".recall_dirty"
    if not dirty.exists() or not config.background_path.exists():
        return ""
    text = _read_text(config.background_path).strip()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    if consume_dirty:
        dirty.unlink(missing_ok=True)
    return text


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""