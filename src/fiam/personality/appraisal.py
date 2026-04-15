"""
Appraisal — Goals × Events → State.

After events are extracted, embedded, stored, and typed, this module
evaluates how the session relates to the AI's goals and updates
``state.md`` with a new psychological snapshot.

Two paths:
  - *full appraisal*: LLM reads goals + state + events → writes new state
  - *passive decay*: no LLM call, tension drifts toward baseline 0.3
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord
from fiam.extractor.signals import SessionSignals


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def run_appraisal(
    config: FiamConfig,
    events: list[EventRecord],
    signals: SessionSignals,
) -> None:
    """Decide whether to run full LLM appraisal or passive decay."""
    max_intensity = max((e.intensity for e in events), default=0.0)
    now = datetime.now(timezone.utc)

    if max_intensity < 0.35 and not signals.any_flagged():
        _passive_decay(config, now)
    else:
        _llm_appraise(config, events, signals, now)


# ------------------------------------------------------------------
# Full LLM appraisal
# ------------------------------------------------------------------

def _llm_appraise(
    config: FiamConfig,
    events: list[EventRecord],
    signals: SessionSignals,
    now: datetime,
) -> None:
    """Call LLM to appraise events against goals and rewrite state.md."""
    from fiam.prompts import load

    template = load("appraisal")

    goals_text = ""
    if config.goals_path.exists():
        goals_text = config.goals_path.read_text(encoding="utf-8").strip()
    if not goals_text:
        goals_text = "(no goals set yet)"

    previous_state = ""
    if config.state_path.exists():
        previous_state = config.state_path.read_text(encoding="utf-8").strip()
    if not previous_state:
        previous_state = "(none — first appraisal)"

    events_block = _format_events(events)

    sig = signals.to_dict()
    prompt = template.format(
        ai_name=config.ai_name or "the AI",
        goals_text=goals_text,
        previous_state=previous_state,
        events_block=events_block,
        volatility=f"{sig['volatility']:.2f}",
        volatility_flag=" ⚡ flagged" if sig["volatility_flag"] else "",
        length_delta=f"{sig['length_delta']:.2f}",
        length_delta_flag=" ⚡ flagged" if sig["length_delta_flag"] else "",
        density=f"{sig['density']:.1f}",
        temperature_gap=f"{sig['temperature_gap']:.2f}",
        temp_gap_flag=" ⚡ flagged" if sig["temperature_gap_flag"] else "",
    )

    result = _call_llm(prompt, config)

    state_text = _render_state_md(result, now)
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(state_text, encoding="utf-8")

    if config.debug_mode:
        print(f"[appraisal] mood={result.get('mood')} "
              f"tension={result.get('tension')} → {config.state_path}")


# ------------------------------------------------------------------
# Passive decay (no LLM)
# ------------------------------------------------------------------

def _passive_decay(config: FiamConfig, now: datetime) -> None:
    """Nudge tension toward 0.3 when session was low-intensity."""
    if not config.state_path.exists():
        return
    text = config.state_path.read_text(encoding="utf-8")
    m = re.search(r"^tension:\s*([0-9.]+)", text, re.MULTILINE)
    if not m:
        return
    current = float(m.group(1))
    new_tension = current + (0.3 - current) * 0.15
    new_text = re.sub(
        r"^tension:\s*[0-9.]+",
        f"tension: {new_tension:.2f}",
        text,
        flags=re.MULTILINE,
    )
    new_text = re.sub(
        r"^updated:\s*.+",
        f"updated: {now.strftime('%Y-%m-%d')}",
        new_text,
        flags=re.MULTILINE,
    )
    config.state_path.write_text(new_text, encoding="utf-8")

    if config.debug_mode:
        print(f"[appraisal] passive decay: tension {current:.2f} → {new_tension:.2f}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _format_events(events: list[EventRecord]) -> str:
    lines = []
    for ev in events:
        preview = ev.body.strip().replace("\n", " ")[:160]
        if len(ev.body.strip()) > 160:
            preview += "..."
        lines.append(f"- **{ev.filename}** (i={ev.intensity:.2f}): {preview}")
    return "\n".join(lines)


def _render_state_md(result: dict[str, Any], now: datetime) -> str:
    mood = str(result.get("mood", "unknown")).strip()
    tension = float(result.get("tension", 0.5))
    tension = max(0.0, min(1.0, tension))
    reflection = str(result.get("reflection", "")).strip()
    goal_note = str(result.get("goal_note", "")).strip()

    lines = [
        "---",
        f"mood: {mood}",
        f"tension: {tension:.2f}",
        f"updated: {now.strftime('%Y-%m-%d')}",
        "---",
        "",
        reflection,
    ]
    if goal_note:
        lines += ["", f"*{goal_note}*"]
    return "\n".join(lines) + "\n"


def _call_llm(prompt: str, config: FiamConfig) -> dict[str, Any]:
    """Call the configured LLM API and return parsed JSON."""
    import urllib.request

    api_key = os.environ.get(config.graph_edge_api_key_env, "")
    if not api_key:
        raise RuntimeError(f"Missing env var: {config.graph_edge_api_key_env}")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = json.dumps({
        "model": config.graph_edge_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 512,
    }).encode()

    req = urllib.request.Request(
        f"{config.graph_edge_base_url.rstrip('/')}/v1/chat/completions",
        data=body,
        headers=headers,
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    content = data["choices"][0]["message"]["content"]

    # Extract JSON from markdown code block if present
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if m:
        content = m.group(1)

    return json.loads(content)
