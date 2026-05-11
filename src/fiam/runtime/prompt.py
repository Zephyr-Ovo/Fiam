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


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict)
        ).strip()
    return str(content or "").strip()


def build_api_messages(
    config: "FiamConfig",
    user_text: str,
    *,
    channel: str = "api",
    include_recall: bool = True,
    consume_recall_dirty: bool = True,
    consume_carryover: bool | None = None,
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
    # without polluting the persisted user text in flow.jsonl. Emitted as a
    # standalone system block so the recall-routing in build_plain_prompt_parts
    # still sees pure "[recall]\n..." prefix.
    from fiam.runtime.turns import normalize_channel
    canon = normalize_channel(channel)
    if canon:
        messages.append(_system_block(f"[context]\nuser_channel={canon}", cache=False))
    dynamic_parts: list[str] = []
    if include_recall:
        recall = load_recall_context(config, consume_dirty=consume_recall_dirty)
        if recall:
            dynamic_parts.append(f"[recall]\n{recall}")
    consume_co = consume_recall_dirty if consume_carryover is None else bool(consume_carryover)
    carryover = load_carryover_context(config, consume=consume_co)
    if carryover:
        dynamic_parts.append(f"[carryover]\n{carryover}")
    # [recent_conversation] — last few transcript turns for THIS channel,
    # so api-side picks up cc-side tool work that isn't in carryover.
    recent = load_recent_conversation_context(config, channel)
    if recent:
        dynamic_parts.append(f"[recent_conversation]\n{recent}")
    if extra_context.strip():
        dynamic_parts.append(extra_context.strip())
    if dynamic_parts:
        messages.append(_system_block("\n\n".join(dynamic_parts), cache=False))

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
    consume_carryover: bool | None = None,
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
        consume_carryover=consume_carryover,
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


def _transcript_channel_slug(channel: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (channel or "chat").strip().lower()).strip("_")
    return slug or "chat"


def _format_recent_turn(rec: dict, *, tool_result_chars: int = 800) -> str:
    """Render one transcript record as markdown for [recent_conversation].

    AI turns include their tool calls inline so the next runtime can see what
    cc actually read/did. Result text is truncated per-tool to keep the block
    bounded.
    """
    role = str(rec.get("role") or "ai")
    runtime = str(rec.get("runtime") or "")
    text = str(rec.get("raw_text") or rec.get("text") or "").strip()
    header_runtime = f" runtime={runtime}" if runtime else ""
    header = f"## {role}{header_runtime}"
    body_parts: list[str] = []
    tools = rec.get("tool_calls_summary") or []
    if isinstance(tools, list):
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = str(t.get("tool_name") or "tool")
            inp = str(t.get("input_summary") or "").strip().replace("\n", " ")
            full = str(t.get("result_full") or "").strip()
            summary = str(t.get("result_summary") or "").strip()
            output = full or summary
            if len(output) > tool_result_chars:
                output = output[:tool_result_chars] + f"… [truncated, {len(output) - tool_result_chars} more chars]"
            is_err = bool(t.get("is_error"))
            tag = "tool!" if is_err else "tool"
            line = f"- {tag} {name}"
            if inp:
                line += f" ← {inp[:200]}"
            if output:
                # indent multi-line output for readability
                indented = output.replace("\n", "\n    ")
                line += f"\n    {indented}"
            body_parts.append(line)
    if text:
        body_parts.append(text)
    if not body_parts:
        return ""
    return f"{header}\n" + "\n".join(body_parts)


def load_recent_conversation_context(
    config: "FiamConfig",
    channel: str,
    *,
    max_turns: int = 8,
    max_chars: int = 6000,
) -> str:
    """Read transcript tail for ``channel`` and format as markdown context.

    Used by build_api_messages so api-side sees cc-side tool work (which
    isn't captured in carryover.md). Truncates oldest first if the total
    exceeds ``max_chars``.
    """
    import json as _json

    slug = _transcript_channel_slug(channel)
    path = config.home_path / "transcript" / f"{slug}.jsonl"
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    records: list[dict] = []
    for line in lines[-max_turns * 2:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue
        if isinstance(rec, dict):
            records.append(rec)
    records = records[-max_turns:]
    if not records:
        return ""
    rendered = [seg for seg in (_format_recent_turn(r) for r in records) if seg]
    if not rendered:
        return ""
    blob = "\n\n".join(rendered)
    if len(blob) > max_chars:
        # drop oldest until within budget; if still over, hard truncate
        while len(blob) > max_chars and len(rendered) > 1:
            rendered = rendered[1:]
            blob = "[…earlier turns omitted…]\n\n" + "\n\n".join(rendered)
        if len(blob) > max_chars:
            blob = blob[:max_chars] + "\n[…truncated]"
    return blob


def load_carryover_context(config: "FiamConfig", *, consume: bool = True) -> str:
    """Load carryover.md (one-shot session-summary + missed-turns context).

    Mirrors the CC inject.sh hook semantics: read, then truncate. The next
    turn will see an empty file (carryover.md exists but 0-byte) and skip
    injection. New content is written by the server-side session rollover
    or by `_append_carryover` for non-cc turns.

    Set ``consume=False`` for diagnostic / dry-run reads.
    """
    co_path = config.home_path / "carryover.md"
    if not co_path.exists():
        return ""
    try:
        text = co_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if consume:
        try:
            co_path.write_text("", encoding="utf-8")
        except OSError:
            pass
        dirty = config.home_path / ".carryover_dirty"
        try:
            dirty.unlink(missing_ok=True)
        except OSError:
            pass
    return text


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
        # Label the system blocks by content for the UI ("constitution"/"self"/"context"/"recall+carryover")
        label = role
        if role == "system":
            if cache and parts and not any(p.get("label") == "constitution" for p in parts if p.get("role") == "system"):
                label = "constitution"
            elif cache:
                label = "self"
            elif text.startswith("[context]"):
                label = "context"
            elif text.startswith("[recall]") or text.startswith("[carryover]") or "[recall]" in text[:40] or "[carryover]" in text[:40]:
                label = "recall+carryover"
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