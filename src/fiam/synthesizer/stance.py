"""
Stance synthesizer — generates first-person psychological background.

Output is written to recall.md and injected as inner state.
Uses narrative.py for first-person memory synthesis (rule-based or LLM).
"""

from __future__ import annotations

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord
from .narrative import prepare_materials, synthesize_narrative


class StanceSynthesizer:
    def __init__(self, config: FiamConfig) -> None:
        self.config = config

    def generate(
        self,
        retrieved_events: list[EventRecord],
        personality: str = "",
        current_context: str = "",
        session_id: str = "",
    ) -> str:
        """Generate first-person psychological background.

        Prepares materials from retrieved events and synthesizes
        a first-person narrative (rule-based by default, LLM optional).
        Personality text (from self/personality.md) is prepended if present.
        """
        header = (
            f"<!-- fiam synthesis | session {session_id} -->"
            if session_id
            else "<!-- fiam synthesis -->"
        )

        parts: list[str] = [header]

        # Personality section (AI's self-description)
        if personality:
            parts.append(f"\n{personality}")

        if not retrieved_events:
            if not personality:
                parts.append("\nNothing much to hold onto yet. Clean slate.")
            return "\n".join(parts)

        # Prepare materials and synthesize
        materials = prepare_materials(retrieved_events)
        narrative = synthesize_narrative(materials, self.config)

        if not narrative:
            event_count = len(retrieved_events)
            parts.append(f"\nAbout {event_count} things in the background now.")
        else:
            parts.append(f"\n---\n\n{narrative}")

        return "\n".join(parts)
