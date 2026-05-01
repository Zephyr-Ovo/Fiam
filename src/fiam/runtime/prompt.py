"""Prompt assembly for API-backed fiam runtimes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fiam.config import FiamConfig


def build_api_messages(
    config: "FiamConfig",
    user_text: str,
    *,
    source: str = "api",
    include_recall: bool = True,
    consume_recall_dirty: bool = True,
    extra_context: str = "",
) -> list[dict[str, str]]:
    """Build OpenAI-compatible messages using the same identity material as CC."""
    messages: list[dict[str, str]] = []
    static_context = _read_text(config.claude_md_path).strip()
    if static_context:
        messages.append({"role": "system", "content": static_context})

    dynamic_parts: list[str] = []
    self_context = load_self_context(config)
    if self_context:
        dynamic_parts.append(f"[self]\n{self_context}")

    if include_recall:
        recall = load_recall_context(config, consume_dirty=consume_recall_dirty)
        if recall:
            dynamic_parts.append(f"[recall]\n{recall}")

    if extra_context.strip():
        dynamic_parts.append(extra_context.strip())

    if dynamic_parts:
        messages.append({"role": "system", "content": "\n\n".join(dynamic_parts)})

    messages.append({"role": "user", "content": f"[wake:{source}] {user_text.strip()}"})
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