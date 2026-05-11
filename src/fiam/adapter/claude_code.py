"""
Claude Code JSONL adapter.

Parses Claude Code's JSONL session files into fiam's generic turn format,
and (v2) into Beat objects for the flow.jsonl narrative stream.

JSONL format:
  {"type":"user",      "message":{"role":"user","content":"..."}, ...}
  {"type":"assistant", "message":{"role":"assistant","content":[...]}, ...}
  {"type":"attachment", "parentUuid":"<user_uuid>",
   "attachment":{"type":"hook_additional_context","content":["..."],...}}

Handles:
  - Deduplication of assistant messages by ID (CC emits partials + final)
  - Merging thinking blocks from earlier partial lines
  - Skipping tool-result messages (content is a list, not string)
  - Byte-offset incremental parsing for daemon polling
  - Graceful handling of mid-write incomplete lines
  - Hook-injected additionalContext (type: "attachment"):
      [recall] sections are EXCLUDED from events (anti-recursion)
      [external] sections are preserved as inbox_context on the parent turn
  - (v2) tool_use blocks → action beats
    - (v2) routing markers [→target:Name] → routed beats
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from fiam.runtime.turns import (
    parse_ts as _parse_ts,
    speaker_label as _speaker_label,
    speaker_text as _speaker_text,
    split_routed_text as _extract_routed,
)

if TYPE_CHECKING:
if TYPE_CHECKING:
    from fiam.store.beat import Beat
    from fiam.markers import OutboundMarker

# XML tags injected by Claude Code infrastructure (Agent Teams, hooks, etc.)
# These look like user messages but are system-generated — must be filtered.
_SYSTEM_TAG_RE = re.compile(
    r"^<(?:teammate-message|local-command-caveat|local-command-stdout|"
    r"command-name|command-message|command-args|task-notification|"
    r"system-reminder|user-prompt-submit-hook)[>\s/]",
    re.IGNORECASE,
)

# Regex to extract [external] section from hook additionalContext
_INBOX_SECTION_RE = re.compile(
    r"\[external\]\s*\n(.*?)(?=\[recall\]|\Z)",
    re.DOTALL,
)

# Regex to detect [recall] section (we strip this entirely)
_RECALL_SECTION_RE = re.compile(
    r"\[recall\]\s*\n(.*?)(?=\[external\]|\Z)",
    re.DOTALL,
)

def _is_system_message(text: str) -> bool:
    """Return True if *text* is a system-injected user turn, not a real human message."""
    return bool(_SYSTEM_TAG_RE.match(text))


class ClaudeCodeAdapter:
    """Parse Claude Code JSONL session files."""

    def parse(self, source: Path) -> list[dict[str, str]]:
        """Parse all turns from a JSONL file."""
        turns, _ = self.parse_incremental(source, 0)
        return turns

    def _extract_inbox_from_attachment(self, content_text: str) -> str:
        """Extract [external] section from hook additionalContext, ignoring [recall].

        Returns only external message content. Recall is deliberately excluded to prevent
        memory fragments from re-entering the event graph (anti-recursion / 套娃).
        """
        if "[external]" not in content_text:
            return ""
        match = _INBOX_SECTION_RE.search(content_text)
        return match.group(1).strip() if match else ""

    def parse_incremental(
        self, source: Path, byte_offset: int = 0,
    ) -> tuple[list[dict[str, str]], int]:
        """Parse JSONL starting from *byte_offset*.

        Returns (turns, new_byte_offset).
        If *byte_offset* exceeds file size (file was replaced), resets to 0.
        Only advances offset past successfully parsed lines — incomplete
        trailing lines (e.g. mid-write crash) are retried next poll.
        """
        size = source.stat().st_size
        if byte_offset > size:
            byte_offset = 0
        if byte_offset >= size:
            return [], byte_offset

        with open(source, "rb") as f:
            f.seek(byte_offset)
            raw = f.read()

        safe_offset = byte_offset

        user_turns: list[tuple[int, dict[str, str]]] = []
        # Map user entry uuid → index in user_turns for attachment linking
        user_uuid_to_idx: dict[str, int] = {}
        assistant_by_msg_id: dict[str, dict] = {}
        assistant_order: dict[str, int] = {}
        order = 0

        pos = 0
        for raw_line in raw.split(b"\n"):
            line_end = pos + len(raw_line) + 1  # +1 for the \n delimiter
            line_text = raw_line.decode("utf-8", errors="replace").strip()
            pos = line_end

            if not line_text:
                safe_offset = byte_offset + pos
                continue
            try:
                obj = json.loads(line_text)
            except json.JSONDecodeError:
                # Incomplete line — stop advancing offset here
                break

            # Line parsed OK — advance safe offset
            safe_offset = byte_offset + pos

            line_type = obj.get("type", "")

            # ── Attachment (hook-injected additionalContext) ──
            if line_type == "attachment":
                attachment = obj.get("attachment", {})
                if attachment.get("type") != "hook_additional_context":
                    continue
                parent_uuid = obj.get("parentUuid", "")
                content_list = attachment.get("content", [])
                content_text = "\n".join(
                    c if isinstance(c, str) else str(c)
                    for c in content_list
                )
                inbox_text = self._extract_inbox_from_attachment(content_text)
                if inbox_text and parent_uuid and parent_uuid in user_uuid_to_idx:
                    idx = user_uuid_to_idx[parent_uuid]
                    _, turn = user_turns[idx]
                    existing = turn.get("inbox_context", "")
                    turn["inbox_context"] = (
                        (existing + "\n" + inbox_text).strip()
                        if existing else inbox_text
                    )
                continue

            if line_type not in ("user", "assistant"):
                continue

            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content", "")

            if line_type == "user":
                if not isinstance(content, str):
                    continue
                text = content.strip()
                if text and not _is_system_message(text):
                    turn: dict[str, str] = {"role": "user", "text": text}
                    ts = obj.get("timestamp", "")
                    if ts:
                        turn["timestamp"] = ts
                    user_turns.append((order, turn))
                    # Track uuid for attachment linking
                    uuid = obj.get("uuid") or message.get("id", "")
                    if uuid:
                        user_uuid_to_idx[uuid] = len(user_turns) - 1
                    order += 1
            elif line_type == "assistant":
                if not isinstance(content, list):
                    continue
                msg_id = message.get("id", "")
                text_parts = [b.get("text", "").strip() for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                thinking_parts = [b.get("thinking", "").strip() for b in content
                                  if isinstance(b, dict) and b.get("type") == "thinking"]
                text = "\n".join(p for p in text_parts if p)
                thinking = "\n".join(p for p in thinking_parts if p)
                if not text and not thinking:
                    continue
                if msg_id and msg_id in assistant_by_msg_id:
                    existing = assistant_by_msg_id[msg_id]
                    if thinking:
                        prev = existing.get("thinking", "")
                        existing["thinking"] = (prev + "\n" + thinking).strip() if prev else thinking
                    if text:
                        existing["text"] = text
                else:
                    entry: dict[str, str] = {"role": "assistant", "text": text}
                    if thinking:
                        entry["thinking"] = thinking
                    ts = obj.get("timestamp", "")
                    if ts:
                        entry["timestamp"] = ts
                    key = msg_id or f"_anon_{order}"
                    assistant_by_msg_id[key] = entry
                    assistant_order[key] = order
                    order += 1

        all_turns: list[tuple[int, dict[str, str]]] = list(user_turns)
        for mid, entry in assistant_by_msg_id.items():
            if entry.get("text"):
                all_turns.append((assistant_order[mid], entry))
        all_turns.sort(key=lambda t: t[0])

        return [turn for _, turn in all_turns], safe_offset

    # ==================================================================
    # v2: parse CC JSONL → Beat sequence for flow.jsonl
    # ==================================================================

    def parse_beats(
        self,
        source: Path,
        byte_offset: int = 0,
        *,
        user_name: str = "zephyr",
    ) -> tuple[list["Beat"], int]:
        """Parse JSONL into Beat objects for the narrative stream.

        Unlike parse_incremental (which produces turn dicts), this method:
        - Includes tool_use blocks as action beats
        - Splits routing markers [→target:Name] into separate beats
        - Skips recall/inbox attachments (recall doesn't enter flow; inbox replaced by direct flow)
        - Produces Beat objects ready for flow.jsonl

        Returns (beats, new_byte_offset).
        """
        from fiam.store.beat import Beat

        size = source.stat().st_size
        if byte_offset > size:
            byte_offset = 0
        if byte_offset >= size:
            return [], byte_offset

        with open(source, "rb") as f:
            f.seek(byte_offset)
            raw = f.read()

        safe_offset = byte_offset
        # Collect entries in order; each entry is (order, Beat)
        entries: list[tuple[int, Beat]] = []
        # Dedup assistant messages (CC emits partials then final)
        asst_text_by_id: dict[str, str] = {}
        asst_thinking_by_id: dict[str, str] = {}
        asst_tools_by_id: dict[str, list[str]] = {}
        asst_ts_by_id: dict[str, str] = {}
        asst_order: dict[str, int] = {}
        order = 0
        user_label = _speaker_label(user_name, "zephyr")

        pos = 0
        for raw_line in raw.split(b"\n"):
            line_end = pos + len(raw_line) + 1
            line_text = raw_line.decode("utf-8", errors="replace").strip()
            pos = line_end

            if not line_text:
                safe_offset = min(byte_offset + pos, size)
                continue
            try:
                obj = json.loads(line_text)
            except json.JSONDecodeError:
                break
            safe_offset = min(byte_offset + pos, size)

            line_type = obj.get("type", "")

            # Skip attachments entirely in beat mode
            # (recall shouldn't enter flow; inbox is replaced by direct beats)
            if line_type == "attachment":
                continue

            if line_type not in ("user", "assistant"):
                continue

            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content", "")
            ts = obj.get("timestamp", "")

            if line_type == "user":
                if not isinstance(content, str):
                    continue
                text = content.strip()
                if text and not _is_system_message(text):
                    entries.append((order, Beat(
                        t=_parse_ts(ts),
                        actor="user",
                        channel="cc",
                        kind="message",
                        content=text,
                    )))
                    order += 1

            elif line_type == "assistant":
                if not isinstance(content, list):
                    continue
                msg_id = message.get("id", "") or f"_anon_{order}"

                # Extract text blocks
                text_parts = [b.get("text", "").strip() for b in content
                              if isinstance(b, dict) and b.get("type") == "text"]
                text = "\n".join(p for p in text_parts if p)
                thinking_parts = [b.get("thinking", "").strip() for b in content
                                  if isinstance(b, dict) and b.get("type") == "thinking"]
                thinking = "\n".join(p for p in thinking_parts if p)

                # Extract tool_use blocks
                tool_descs: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        # Brief summary: tool name + key argument
                        brief = _tool_brief(name, inp)
                        tool_descs.append(brief)

                # Dedup: keep latest text, accumulate tools
                if msg_id in asst_text_by_id:
                    if text:
                        asst_text_by_id[msg_id] = text
                    if thinking:
                        prev = asst_thinking_by_id.get(msg_id, "")
                        asst_thinking_by_id[msg_id] = (prev + "\n" + thinking).strip() if prev else thinking
                    asst_tools_by_id[msg_id].extend(tool_descs)
                else:
                    asst_text_by_id[msg_id] = text
                    asst_thinking_by_id[msg_id] = thinking
                    asst_tools_by_id[msg_id] = tool_descs
                    asst_ts_by_id[msg_id] = ts
                    asst_order[msg_id] = order
                    order += 1

        # Assemble assistant entries into beats
        for mid in asst_text_by_id:
            ts_str = asst_ts_by_id[mid]
            t = _parse_ts(ts_str)
            text = asst_text_by_id[mid]
            thinking = asst_thinking_by_id.get(mid, "")
            tools = asst_tools_by_id[mid]

            # Split routing markers from assistant text
            routed, remaining = _extract_routed(text)

            # Tool_use → action beat(s)
            if tools:
                tool_text = "; ".join(tools)
                entries.append((asst_order[mid], Beat(
                    t=t, actor="ai", channel="cc", kind="action",
                    content=tool_text, runtime="cc",
                )))

            # Native thinking (Claude extended thinking) → kind=think on cc.
            if thinking:
                entries.append((asst_order[mid], Beat(
                    t=t,
                    actor="ai",
                    channel="cc",
                    kind="think",
                    content=thinking,
                    runtime="cc",
                    meta={"source": "native"},
                )))

            # Routed messages → dispatch beats keyed by target channel.
            for marker in routed:
                entries.append((asst_order[mid], Beat(
                    t=t,
                    actor="ai",
                    channel=marker.channel,
                    kind="message",
                    content=marker.body.strip(),
                    runtime="cc",
                )))

            # Remaining CC dialogue text (after stripping routed parts)
            if remaining.strip():
                entries.append((asst_order[mid], Beat(
                    t=t, actor="ai", channel="cc", kind="message",
                    content=remaining.strip(), runtime="cc",
                )))

        entries.sort(key=lambda e: e[0])
        return [beat for _, beat in entries], safe_offset


# ------------------------------------------------------------------
# Helpers for beat parsing
# ------------------------------------------------------------------

def _tool_brief(name: str, inp: dict) -> str:
    """Produce a brief natural-language summary of a tool_use block."""
    if name in ("Read", "Glob"):
        path = inp.get("path") or inp.get("pattern", "")
        return f"[{name}] {path}" if path else f"[{name}]"
    if name == "Write":
        path = inp.get("path", "")
        return f"[Write] {path}" if path else "[Write]"
    if name == "Edit":
        path = inp.get("path") or inp.get("file_path", "")
        return f"[Edit] {path}" if path else "[Edit]"
    if name == "Bash":
        cmd = inp.get("command", "")
        # Truncate long commands
        if len(cmd) > 80:
            cmd = cmd[:77] + "..."
        return f"[Bash] {cmd}" if cmd else "[Bash]"
    # Generic fallback
    return f"[{name}]"


