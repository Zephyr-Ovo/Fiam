"""
Session side-channel signal extractor.

Extracts structural conversation metadata that reveals dynamics
beyond what individual turn content captures:

  - volatility:       text intensity range across turns (max - min)
  - length_delta:     max ratio of reply length change vs session average
  - density:          conversation pairs per hour
  - temperature_gap:  |mean_user_intensity - mean_assistant_intensity|

These signals are injected into the pre-session background when abnormal
and stored in the session report for longitudinal analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fiam.classifier.text_intensity import text_intensity


@dataclass
class SessionSignals:
    volatility: float       # [0.0, 1.0] — intensity range across all turns
    length_delta: float     # ≥ 1.0 — max reply length / session avg length
    density: float          # pairs per hour
    temperature_gap: float  # |mean_user_intensity - mean_assistant_intensity|

    # Thresholds for flagging
    volatility_flag: bool = False    # > 0.4
    length_delta_flag: bool = False  # > 3.0
    temperature_gap_flag: bool = False  # > 0.3

    def any_flagged(self) -> bool:
        return self.volatility_flag or self.length_delta_flag or self.temperature_gap_flag

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "volatility": round(self.volatility, 4),
            "length_delta": round(self.length_delta, 4),
            "density": round(self.density, 4),
            "temperature_gap": round(self.temperature_gap, 4),
            "volatility_flag": self.volatility_flag,
            "length_delta_flag": self.length_delta_flag,
            "temperature_gap_flag": self.temperature_gap_flag,
        }


_VOLATILITY_THRESHOLD = 0.4
_LENGTH_DELTA_THRESHOLD = 3.0
_TEMP_GAP_THRESHOLD = 0.3


def extract_session_signals(
    conversation: list[dict[str, str]],
    session_start: datetime | None = None,
    session_end: datetime | None = None,
) -> SessionSignals:
    """Compute side-channel signals from a full conversation.

    *conversation* is the same list[dict] used by the pipeline:
    each dict has 'role' and 'text' keys.

    Uses text_intensity heuristic instead of emotion classification.
    """
    if not conversation:
        return SessionSignals(
            volatility=0.0, length_delta=1.0, density=0.0, temperature_gap=0.0,
        )

    # Separate by role
    user_texts = [t["text"] for t in conversation if t.get("role") == "user"]
    asst_texts = [t["text"] for t in conversation if t.get("role") == "assistant"]

    # --- Intensity per turn (pure heuristic, no model) ---
    user_intensities = [text_intensity(t) for t in user_texts]
    asst_intensities = [text_intensity(t) for t in asst_texts]
    all_intensities = user_intensities + asst_intensities

    # Volatility: range of intensity across all turns
    volatility = (max(all_intensities) - min(all_intensities)) if all_intensities else 0.0

    # Temperature gap: |mean user intensity - mean assistant intensity|
    mean_user = sum(user_intensities) / len(user_intensities) if user_intensities else 0.0
    mean_asst = sum(asst_intensities) / len(asst_intensities) if asst_intensities else 0.0
    temperature_gap = abs(mean_user - mean_asst)

    # --- Length delta ---
    all_lengths = [len(t["text"]) for t in conversation if t.get("text")]
    avg_len = sum(all_lengths) / len(all_lengths) if all_lengths else 1.0
    length_delta = max(all_lengths) / avg_len if avg_len > 0 else 1.0

    # --- Density ---
    pair_count = min(len(user_texts), len(asst_texts))
    if session_start and session_end:
        duration_hours = (session_end - session_start).total_seconds() / 3600.0
    else:
        # Estimate: ~2 min per pair is a rough heuristic
        duration_hours = pair_count * 2.0 / 60.0
    density = pair_count / duration_hours if duration_hours > 0 else 0.0

    return SessionSignals(
        volatility=volatility,
        length_delta=length_delta,
        density=density,
        temperature_gap=temperature_gap,
        volatility_flag=volatility > _VOLATILITY_THRESHOLD,
        length_delta_flag=length_delta > _LENGTH_DELTA_THRESHOLD,
        temperature_gap_flag=temperature_gap > _TEMP_GAP_THRESHOLD,
    )
