"""
Human-readable session report generator.

Pure Python. Zero LLM calls. Reads existing data, formats to Markdown.
Generates ONE .md file per post_session run:
  logs/sessions/{session_id}/report.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from fiam.extractor.signals import SessionSignals


def generate(
    config: FiamConfig,
    session_id: str,
    *,
    conversation: list[dict[str, str]],
    emotion_results: list[dict[str, Any]],
    events: list[EventRecord],
    embedding_stats: list[dict[str, Any]],
    all_events: list[EventRecord],
    signals: SessionSignals | None = None,
) -> Path:
    """Generate a comprehensive session report as Markdown.

    Returns the path to the written report file.
    """
    report_dir = config.logs_dir / "sessions" / session_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"

    sections: list[str] = []

    # Header
    sections.append(f"# fiam session report\n")
    sections.append(f"**Session:** {session_id}  ")
    sections.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")

    # --- Conversation ---
    sections.append("---\n## Conversation\n")
    for turn in conversation:
        role = turn.get("role", "unknown")
        text = turn.get("text", "")
        thinking = turn.get("thinking", "")
        if thinking:
            sections.append(f"### [{role}] [thinking]\n")
            sections.append(f"```\n{thinking}\n```\n")
        sections.append(f"### [{role}]\n")
        sections.append(f"{text}\n")

    # --- Emotion Analysis ---
    sections.append("---\n## Emotion Analysis\n")
    if emotion_results:
        for i, emo in enumerate(emotion_results):
            sections.append(f"### Event {i + 1}\n")
            sections.append(f"- **Valence:** {emo.get('valence', 'N/A')}")
            sections.append(f"- **Arousal:** {emo.get('arousal', 'N/A')}")
            sections.append(f"- **Confidence:** {emo.get('confidence', 'N/A')}")
            if "corrected" in emo:
                sections.append(f"- **RLHF corrected:** {emo['corrected']}")
            sections.append("")
    else:
        sections.append("No emotion analysis results.\n")

    # --- Extracted Events ---
    sections.append("---\n## Extracted Events\n")
    if events:
        for ev in events:
            sections.append(f"### {ev.filename}\n")
            sections.append(f"- **Valence:** {ev.valence:.4f}")
            sections.append(f"- **Arousal:** {ev.arousal:.4f}")
            sections.append(f"- **Confidence:** {ev.confidence:.4f}")
            sections.append(f"- **Time:** {ev.time.isoformat()}")
            sections.append(f"\n{ev.body}\n")
    else:
        sections.append("No events extracted.\n")

    # --- Embedding Stats ---
    sections.append("---\n## Embedding Stats\n")
    if embedding_stats:
        for i, stats in enumerate(embedding_stats):
            ev_id = stats.get("event_id", f"event_{i + 1}")
            sections.append(f"### {ev_id}\n")
            sections.append(f"- **Shape:** {stats.get('shape', 'N/A')}")
            sections.append(f"- **Max:** {_fmt(stats.get('max'))}")
            sections.append(f"- **Min:** {_fmt(stats.get('min'))}")
            sections.append(f"- **Mean:** {_fmt(stats.get('mean'))}")
            sections.append(f"- **L2 norm:** {_fmt(stats.get('l2_norm'))}")
            first_32 = stats.get("first_32", [])
            if first_32:
                formatted = ", ".join(_fmt(x) for x in first_32)
                sections.append(f"- **First 32 dims:** [{formatted}]")
            sections.append("")
    else:
        sections.append("No embedding stats available.\n")

    # --- Current Background ---
    sections.append("---\n## Active Background\n")
    bg_path = config.background_path
    if bg_path.exists():
        bg_text = bg_path.read_text(encoding="utf-8")
        sections.append(f"```\n{bg_text.strip()}\n```\n")
    else:
        sections.append("No recall.md found.\n")

    # --- Session Signals ---
    sections.append("---\n## Session Signals\n")
    if signals is not None:
        sd = signals.to_dict()
        flag = lambda k: " ⚠" if sd.get(f"{k}_flag") else ""
        sections.append(f"- **Volatility:** {sd['volatility']:.4f}{flag('volatility')}")
        sections.append(f"- **Length delta:** {sd['length_delta']:.4f}{flag('length_delta')}")
        sections.append(f"- **Density:** {sd['density']:.4f} pairs/hr")
        sections.append(f"- **Temperature gap:** {sd['temperature_gap']:.4f}{flag('temperature_gap')}")
        sections.append("")
    else:
        sections.append("No signal data.\n")

    # --- Home Summary ---
    sections.append("---\n## Home Summary\n")
    sections.append(f"**Total events:** {len(all_events)}\n")
    if all_events:
        sections.append("| Event ID | Valence | Arousal | Confidence | Time |")
        sections.append("|----------|---------|---------|------------|------|")
        for ev in all_events:
            sections.append(
                f"| {ev.filename} | {ev.valence:.4f} | {ev.arousal:.4f} "
                f"| {ev.confidence:.4f} | {ev.time.strftime('%Y-%m-%d %H:%M')} |"
            )
        sections.append("")

    report_text = "\n".join(sections)
    report_path.write_text(report_text, encoding="utf-8")
    return report_path


def _fmt(value: Any) -> str:
    """Format a numeric value as fixed-point (never scientific notation)."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)
