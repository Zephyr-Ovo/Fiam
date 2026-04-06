"""
Personality reader — reads the AI's self-authored personality file.

The AI maintains home/self/personality.md as its self-description.
fiam reads this file during pre_session and injects it into the
background context, giving the AI continuity of identity across sessions.

This module only reads — the AI writes the file itself during sessions.
"""

from __future__ import annotations

from fiam.config import FiamConfig


def read_personality(config: FiamConfig) -> str:
    """Read home/self/personality.md and return its content.

    Returns empty string if the file does not exist yet (Day 1).
    """
    path = config.personality_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()
