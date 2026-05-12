"""Prompt assembly for API-backed fiam runtimes.

Layout:
    [system 1] constitution.md       (cache_control if non-empty)
    [system 2] self/*.md combined    (cache_control if non-empty)
    [system 3] [recall] + extras     (no cache, churn-prone)
    [user]    [source:from_name] text

cache_control uses the OpenAI-compatible structured-blocks form. Providers
that don't support prefix caching (e.g. DeepSeek, Gemini) silently ignore
the field per OpenRouter's pass-through spec; Anthropic-family models honor
it (5-minute ephemeral TTL, byte-identical prefix required).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fiam.channels import normalize_channel

if TYPE_CHECKING:
    from fiam.config import FiamConfig


def _system_block(text: str, *, cache: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return {"role": "system", "content": [block]}


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict)
        ).strip()
    return str(content or "").strip()


class PromptAssembler:
    """Single prompt boundary for official transcript-shaped model input."""

    def __init__(self, config: "FiamConfig") -> None:
        self.config = config

    def build_messages(
        self,
        user_text: str,
        *,
        channel: str = "api",
        include_recall: bool = True,
        consume_recall_dirty: bool = True,
        extra_context: str = "",
    ) -> list[dict[str, Any]]:
        return build_api_messages(
            self.config,
            user_text,
            channel=channel,
            include_recall=include_recall,
            consume_recall_dirty=consume_recall_dirty,
            extra_context=extra_context,
        )

    def build_plain(
        self,
        user_text: str,
        *,
        channel: str = "app",
        include_recall: bool = True,
        consume_recall_dirty: bool = True,
        extra_context: str = "",
    ) -> str:
        return build_plain_prompt(
            self.config,
            user_text,
            channel=channel,
            include_recall=include_recall,
            consume_recall_dirty=consume_recall_dirty,
            extra_context=extra_context,
        )


def build_api_messages(
    config: "FiamConfig",
    user_text: str,
    *,
    channel: str = "api",
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
    if constitution:
        messages.append(_system_block(constitution, cache=True))

    # system 2 — self/*.md combined (identity / impressions / lessons / commitments)
    self_context = load_self_context(config)
    if self_context:
        messages.append(_system_block(self_context, cache=True))

    # system 3 — recall + extras (churn-prone, no cache)
    # Channel context: tells the model which surface this message came from,
    # without polluting the persisted user text in events. Emitted as a
    # standalone system block so the recall-routing in build_plain_prompt_parts
    # still sees pure "[recall]\n..." prefix.
    canon = normalize_channel(channel)
    if canon:
        messages.append(_system_block(f"[context]\nuser_channel={canon}", cache=False))
    dynamic_parts: list[str] = []
    if include_recall:
        recall = load_recall_context(config, consume_dirty=consume_recall_dirty)
        if recall:
            dynamic_parts.append(f"[recall]\n{recall}")
    if extra_context.strip():
        dynamic_parts.append(extra_context.strip())
    if dynamic_parts:
        messages.append(_system_block("\n\n".join(dynamic_parts), cache=False))

    messages.extend(load_transcript_messages(config, canon))
    messages.append(
        {"role": "user", "content": user_text.strip()}
    )

    # Debug snapshot — record the assembled prompt for the /debug/context UI.
    # Single-writer best-effort; ignored on any error.
    try:
        _write_debug_assembly(config, messages, channel=channel)
    except Exception:
        pass

    return messages


def build_plain_prompt(
    config: "FiamConfig",
    user_text: str,
    *,
    channel: str = "app",
    include_recall: bool = True,
    consume_recall_dirty: bool = True,
    extra_context: str = "",
) -> str:
    """Render the same prompt segments as plain text.

    API and CC backends should see the same constitution/self/recall order.
    """
    system_context, user_prompt = build_plain_prompt_parts(
        config,
        user_text,
        channel=channel,
        include_recall=include_recall,
        consume_recall_dirty=consume_recall_dirty,
        extra_context=extra_context,
    )
    return "\n\n".join(part for part in (system_context, user_prompt) if part)


def build_plain_prompt_parts(
    config: "FiamConfig",
    user_text: str,
    *,
    channel: str = "app",
    include_recall: bool = True,
    consume_recall_dirty: bool = True,
    extra_context: str = "",
) -> tuple[str, str]:
    """Return ``(system_context, user_prompt)`` for non-API runtimes.

    Claude Code can receive ``system_context`` through ``--append-system-prompt``
    and ``user_prompt`` through ``-p``. This preserves the same material/order
    as API messages while letting CC keep its default system prompt and cache
    behavior.
    """
    messages = build_api_messages(
        config,
        user_text,
        channel=channel,
        include_recall=include_recall,
        consume_recall_dirty=consume_recall_dirty,
        extra_context=extra_context,
    )
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages:
        role = message.get("role", "")
        text = _content_text(message.get("content"))
        if not text:
            continue
        if role == "system":
            if text.startswith("[recall]"):
                user_parts.append(text)
            else:
                system_parts.append(text)
        elif role == "user":
            user_parts.append(text)
        else:
            user_parts.append(f"[{role}]\n{text}")
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


def load_self_context(config: "FiamConfig") -> str:
    """Load self/*.md in the same shape as the CC inject hook."""
    self_dir = config.self_dir
    if not self_dir.is_dir():
        return ""
    parts: list[str] = []
    preferred = [
        "identity.md",
        "impressions.md",
        "lessons.md",
        "commitments.md",
    ]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for name in preferred:
        path = self_dir / name
        if path.exists():
            ordered.append(path)
            seen.add(path)
    ordered.extend(path for path in sorted(self_dir.glob("*.md")) if path not in seen)
    for path in ordered:
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


def transcript_path(config: "FiamConfig", channel: str) -> Path:
    canon = normalize_channel(channel)
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", canon).strip("_") or "favilla"
    return config.store_dir / "transcripts" / f"{clean}.jsonl"


def _valid_transcript_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    role = str(message.get("role") or "").strip()
    if role not in {"user", "assistant", "tool"}:
        return None
    out = {k: v for k, v in message.items() if k in {"role", "content", "tool_calls", "tool_call_id", "name"}}
    if "content" not in out and "tool_calls" not in out:
        return None
    out["role"] = role
    if role == "assistant":
        out = _clean_assistant_transcript_message(out)
    return out


def _clean_assistant_transcript_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only model-visible assistant history; strip private/control markers."""
    from fiam.turn import MarkerInterpreter

    content = message.get("content")
    if isinstance(content, str):
        interpretation = MarkerInterpreter().interpret(content)
        if not interpretation.visible_reply and not message.get("tool_calls"):
            return None
        out = dict(message)
        out["content"] = interpretation.visible_reply
        return out
    if isinstance(content, list):
        cleaned_blocks: list[dict[str, Any]] = []
        interpreter = MarkerInterpreter()
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = str(block.get("text") or "")
                visible = interpreter.interpret(text).visible_reply
                if visible:
                    item = dict(block)
                    item["text"] = visible
                    cleaned_blocks.append(item)
            elif block.get("type") in {"tool_use", "tool_result"}:
                cleaned_blocks.append(dict(block))
        if not cleaned_blocks and not message.get("tool_calls"):
            return None
        out = dict(message)
        out["content"] = cleaned_blocks
        return out
    return message


def load_transcript_messages(
    config: "FiamConfig",
    channel: str,
    *,
    max_messages: int = 80,
) -> list[dict[str, Any]]:
    path = transcript_path(config, channel)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max_messages:]:
        try:
            message = _valid_transcript_message(json.loads(line))
        except json.JSONDecodeError:
            continue
        if message:
            out.append(message)
    return out


def append_transcript_messages(
    config: "FiamConfig",
    channel: str,
    messages: list[dict[str, Any]],
) -> None:
    path = transcript_path(config, channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for message in messages:
            clean = _valid_transcript_message(message)
            if clean:
                fh.write(json.dumps(clean, ensure_ascii=False) + "\n")


def trim_transcript_messages(
    config: "FiamConfig",
    channel: str,
    *,
    max_messages: int = 120,
) -> None:
    path = transcript_path(config, channel)
    if not path.exists():
        return
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return
    if len(lines) <= max_messages:
        return
    path.write_text("\n".join(lines[-max_messages:]) + "\n", encoding="utf-8")


def _write_debug_assembly(
    config: "FiamConfig",
    messages: list[dict[str, Any]],
    *,
    channel: str,
) -> None:
    """Record the just-assembled prompt to home/.debug_last_assembly.json.

    Used by /debug/context UI. AI's reply is intentionally NOT included —
    this snapshot is "what the model received", not "what came back".
    """
    import json
    import time

    parts: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")
        text = _content_text(content)
        cache = False
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control"):
                    cache = True
                    break
        # Label the system blocks by content for the UI ("constitution"/"self"/"context"/"recall")
        label = role
        if role == "system":
            if cache and parts and not any(p.get("label") == "constitution" for p in parts if p.get("role") == "system"):
                label = "constitution"
            elif cache:
                label = "self"
            elif text.startswith("[context]"):
                label = "context"
            elif text.startswith("[recall]") or "[recall]" in text[:40]:
                label = "recall"
            else:
                label = "system-extra"
        parts.append({
            "role": role,
            "label": label,
            "cache": cache,
            "length": len(text),
            "text": text,
        })

    snapshot = {
        "timestamp": time.time(),
        "channel": channel,
        "parts": parts,
    }

    out = config.home_path / ".debug_last_assembly.json"
    try:
        out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
