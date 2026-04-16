"""
Self-profile materials generator.

Rather than telling the AI "you are X", we extract structural facts from
its own memory and give it raw material to reason about itself.

Produces ``{home}/self/materials.md`` — a report listing:
  - top-centrality events (repeated interests)
  - high-intensity clusters (emotional hotspots)
  - edge-type distribution (thinking mode)
  - active hours (temporal rhythm)
  - goal-touch history (which goals keep surfacing)

The AI reads this and writes its own ``personality.md`` / ``interests.md``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fiam.config import FiamConfig


def generate_materials(config: FiamConfig) -> Path | None:
    """Produce ``self/materials.md`` and return the path.

    Returns None if there is not enough data yet.
    """
    from fiam.store.home import HomeStore
    from fiam.store.graph_store import GraphStore
    from fiam.retriever.graph import MemoryGraph

    store = HomeStore(config)
    events = store.all_events()
    if len(events) < 5:
        return None

    graph_store = GraphStore(config.graph_jsonl_path)
    edges = graph_store.load_as_dicts()

    graph = MemoryGraph()
    graph.build(events, now=datetime.now(timezone.utc), edges=edges)

    sections: list[str] = []
    sections.append("<!-- Auto-generated from memory. You read, you decide who you are. -->")
    sections.append(f"<!-- Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} -->")
    sections.append("")
    sections.append("# Materials for self-reflection")
    sections.append("")
    sections.append(
        "This file lists structural facts extracted from your memory graph. "
        "It is raw material — patterns you may or may not identify with. "
        "Read, then write `personality.md` / `interests.md` yourself."
    )
    sections.append("")

    # --- Top-centrality events (most connected = repeated concerns) ---
    sections.append("## Most-connected memories")
    sections.append("")
    sections.append("Events you keep coming back to (high graph centrality):")
    sections.append("")
    try:
        import networkx as nx
        centrality = nx.pagerank(graph.G, alpha=0.85, max_iter=100)
        top_c = sorted(centrality.items(), key=lambda t: t[1], reverse=True)[:10]
        for eid, score in top_c:
            ev = next((e for e in events if e.event_id == eid), None)
            if ev is None:
                continue
            sections.append(f"- **{ev.filename}** (centrality={score:.3f}, intensity={ev.intensity:.2f})")
    except Exception as e:
        sections.append(f"*(centrality computation failed: {e})*")
    sections.append("")

    # --- High-intensity events ---
    sections.append("## Highest-intensity moments")
    sections.append("")
    high = sorted(events, key=lambda e: e.intensity, reverse=True)[:8]
    for ev in high:
        sections.append(f"- **{ev.filename}** (intensity={ev.intensity:.2f}, {ev.time.strftime('%Y-%m-%d')})")
    sections.append("")

    # --- Edge-type distribution (thinking mode) ---
    sections.append("## Relation mix")
    sections.append("")
    sections.append("How your memories connect to each other:")
    sections.append("")
    type_counter: Counter[str] = Counter(e.get("type", "unknown") for e in edges)
    total = sum(type_counter.values()) or 1
    for etype, count in type_counter.most_common():
        pct = 100.0 * count / total
        sections.append(f"- `{etype}`: {count} edges ({pct:.0f}%)")
    sections.append("")
    sections.append(
        "*High `causal` = analytical. High `remind` = associative. "
        "High `contrast` = comparative.*"
    )
    sections.append("")

    # --- Temporal rhythm ---
    sections.append("## When you are active")
    sections.append("")
    hour_counter: Counter[int] = Counter(e.time.hour for e in events)
    active_hours = sorted(hour_counter.items())
    peaks = sorted(hour_counter.items(), key=lambda t: t[1], reverse=True)[:3]
    sections.append(f"Peak hours: " + ", ".join(f"{h:02d}:00 ({c} events)" for h, c in peaks))
    sections.append("")

    # --- Goal-touch history ---
    sections.append("## Goal-touch history")
    sections.append("")
    goals_touch = _scan_state_history(config)
    if goals_touch:
        sections.append("Recent `goal_note` entries from your state.md snapshots:")
        sections.append("")
        for line in goals_touch[-10:]:
            sections.append(f"- {line}")
    else:
        sections.append("*(no goal_note history yet — more sessions needed)*")
    sections.append("")

    # --- Write ---
    config.self_dir.mkdir(parents=True, exist_ok=True)
    out = config.self_dir / "materials.md"
    out.write_text("\n".join(sections) + "\n", encoding="utf-8")
    return out


def _scan_state_history(config: FiamConfig) -> list[str]:
    """Scan trajectory JSONLs for tension/mood changes (stand-in for goal_note history)."""
    traj_dir = config.store_dir / "trajectories"
    if not traj_dir.is_dir():
        return []
    import json
    lines: list[str] = []
    for path in sorted(traj_dir.glob("*.jsonl"))[-7:]:  # last 7 days
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                entry = json.loads(raw)
                sa = entry.get("state_after", {})
                mood = sa.get("mood", "")
                tension = sa.get("tension")
                if mood and tension is not None:
                    ts = entry.get("timestamp", "")[:10]
                    lines.append(f"{ts}: mood=`{mood}` tension={tension:.2f}")
        except Exception:
            continue
    return lines
