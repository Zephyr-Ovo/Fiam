"""
Conversation adapters — parse platform-specific logs into fiam's turn format.

Each adapter implements the ConversationAdapter protocol:
  parse(source, offset) → (turns, new_offset)

Turns are list[dict] with keys: role ("user"|"assistant"), text, thinking (optional).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ConversationAdapter(Protocol):
    """Protocol for conversation log parsers.

    Adapters translate platform-specific log formats into fiam's
    generic turn format. To add a new platform, implement this
    protocol and register it in get_adapter().
    """

    def parse(self, source: Path) -> list[dict[str, str]]:
        """Parse all turns from *source*."""
        ...

    def parse_incremental(
        self, source: Path, byte_offset: int = 0,
    ) -> tuple[list[dict[str, str]], int]:
        """Parse turns starting from *byte_offset*.

        Returns (turns, new_byte_offset).
        """
        ...


def get_adapter(name: str = "claude_code") -> ConversationAdapter:
    """Return an adapter instance by platform name."""
    if name == "claude_code":
        from fiam.adapter.claude_code import ClaudeCodeAdapter
        return ClaudeCodeAdapter()
    if name == "claude_web":
        from fiam.adapter.claude_web import ClaudeWebAdapter
        return ClaudeWebAdapter()
    raise ValueError(f"Unknown adapter: {name!r}. Available: claude_code, claude_web")
