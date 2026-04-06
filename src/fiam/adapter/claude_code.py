"""
Claude Code JSONL adapter.

Parses Claude Code's JSONL session files into fiam's generic turn format.

JSONL format:
  {"type":"user",      "message":{"role":"user","content":"..."}, ...}
  {"type":"assistant", "message":{"role":"assistant","content":[...]}, ...}

Handles:
  - Deduplication of assistant messages by ID (CC emits partials + final)
  - Merging thinking blocks from earlier partial lines
  - Skipping tool-result messages (content is a list, not string)
  - Byte-offset incremental parsing for daemon polling
  - Graceful handling of mid-write incomplete lines
"""

from __future__ import annotations

import json
from pathlib import Path


class ClaudeCodeAdapter:
    """Parse Claude Code JSONL session files."""

    def parse(self, source: Path) -> list[dict[str, str]]:
        """Parse all turns from a JSONL file."""
        turns, _ = self.parse_incremental(source, 0)
        return turns

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
                if text:
                    user_turns.append((order, {"role": "user", "text": text}))
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
