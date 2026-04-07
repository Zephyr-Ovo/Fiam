"""
Claude Web export adapter.

Parses the JSON export from claude.ai (Settings → Export Data)
into fiam's generic turn format.

Export format (conversations.json):
  [
    {
      "uuid": "...",
      "name": "...",
      "chat_messages": [
        {
          "uuid": "...",
          "text": "...",
          "content": [
            {"type": "text", "text": "..."},
            {"type": "thinking", "thinking": "..."}
          ],
          "sender": "human" | "assistant",
          "created_at": "2026-04-05T14:00:00Z",
          ...
        },
        ...
      ]
    },
    ...
  ]
"""

from __future__ import annotations

import json
from pathlib import Path


class ClaudeWebAdapter:
    """Parse Claude Web (claude.ai) JSON export files."""

    def parse(self, source: Path) -> list[dict[str, str]]:
        """Parse all turns from a JSON export file.

        If the file contains multiple conversations, they are concatenated
        in chronological order.
        """
        raw = json.loads(source.read_text(encoding="utf-8"))

        # Handle both single-conversation and multi-conversation exports
        conversations: list[dict] = []
        if isinstance(raw, list):
            conversations = raw
        elif isinstance(raw, dict) and "chat_messages" in raw:
            conversations = [raw]
        else:
            return []

        all_turns: list[dict[str, str]] = []
        for conv in conversations:
            all_turns.extend(self._parse_conversation(conv))

        return all_turns

    def parse_incremental(
        self, source: Path, byte_offset: int = 0,
    ) -> tuple[list[dict[str, str]], int]:
        """JSON files are always parsed in full; offset tracks completion."""
        size = source.stat().st_size
        if byte_offset >= size:
            return [], byte_offset
        return self.parse(source), size

    def parse_multi(self, source: Path) -> list[tuple[str, list[dict[str, str]]]]:
        """Parse and return each conversation separately.

        Returns list of (conversation_name, turns) tuples,
        sorted by the first message's created_at.
        """
        raw = json.loads(source.read_text(encoding="utf-8"))

        conversations: list[dict] = []
        if isinstance(raw, list):
            conversations = raw
        elif isinstance(raw, dict) and "chat_messages" in raw:
            conversations = [raw]
        else:
            return []

        result: list[tuple[str, list[dict[str, str]]]] = []
        for conv in conversations:
            name = conv.get("name", "") or conv.get("uuid", "unnamed")
            turns = self._parse_conversation(conv)
            if turns:
                result.append((name, turns))

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_conversation(conv: dict) -> list[dict[str, str]]:
        """Convert one conversation object into fiam turn format."""
        messages = conv.get("chat_messages", [])
        turns: list[dict[str, str]] = []

        for msg in messages:
            sender = msg.get("sender", "")
            if sender == "human":
                role = "user"
            elif sender == "assistant":
                role = "assistant"
            else:
                continue

            # Extract text and thinking from content blocks
            content_blocks = msg.get("content", [])
            text_parts: list[str] = []
            thinking_parts: list[str] = []

            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    t = block.get("text", "").strip()
                    if t:
                        text_parts.append(t)
                elif block_type == "thinking":
                    t = block.get("thinking", "").strip()
                    if t:
                        thinking_parts.append(t)

            # Fallback: use top-level "text" field if content blocks are empty
            if not text_parts:
                fallback = msg.get("text", "").strip()
                if fallback:
                    text_parts.append(fallback)

            text = "\n".join(text_parts)
            if not text:
                continue

            entry: dict[str, str] = {"role": role, "text": text}
            if thinking_parts:
                entry["thinking"] = "\n".join(thinking_parts)

            turns.append(entry)

        return turns
