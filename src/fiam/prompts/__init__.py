"""Prompt loader — read .txt prompts from src/fiam/prompts/."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load(name: str) -> str:
    """Load a prompt by name (without .txt extension)."""
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip()
