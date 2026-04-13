"""
Graph edge store — JSONL-backed edge storage for the memory graph.

Edges live in store/graph.jsonl, one JSON object per line:
    {"src": "ev_0408_001", "dst": "ev_0408_002", "type": "temporal", "weight": 0.85}

This replaces the previous design where links were embedded in each
event's YAML frontmatter.  Benefits:
  - Events are clean (no noisy links array in frontmatter)
  - Single source of truth for graph topology
  - Easy to rebuild / inspect / pipe to DS for edge typing
  - O(1) append, O(n) full load — fine for <100k edges
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Edge:
    src: str
    dst: str
    type: str       # "temporal" | "semantic" | "causal" | "remind" | "contrast" | "elaboration"
    weight: float   # [0.0, 1.0]
    reason: str = ""  # optional — DS-generated explanation

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "src": self.src,
            "dst": self.dst,
            "type": self.type,
            "weight": round(self.weight, 4),
        }
        if self.reason:
            d["reason"] = self.reason
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Edge":
        return Edge(
            src=d["src"],
            dst=d["dst"],
            type=d.get("type", "temporal"),
            weight=float(d.get("weight", 0.5)),
            reason=d.get("reason", ""),
        )


class GraphStore:
    """Read/write interface for store/graph.jsonl."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def load_all(self) -> list[Edge]:
        """Load all edges from graph.jsonl."""
        if not self._path.exists():
            return []
        edges: list[Edge] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                edges.append(Edge.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                continue  # skip malformed lines
        return edges

    def load_as_dicts(self) -> list[dict[str, Any]]:
        """Load all edges as raw dicts (for MemoryGraph.build compatibility)."""
        return [e.to_dict() for e in self.load_all()]

    def edges_for(self, event_id: str) -> list[Edge]:
        """Return all edges involving a specific event (as src or dst)."""
        return [e for e in self.load_all() if e.src == event_id or e.dst == event_id]

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def append(self, edges: list[Edge]) -> int:
        """Append edges to graph.jsonl.  Returns count written."""
        if not edges:
            return 0
        with open(self._path, "a", encoding="utf-8") as f:
            for edge in edges:
                f.write(json.dumps(edge.to_dict(), ensure_ascii=False) + "\n")
        return len(edges)

    def append_one(self, edge: Edge) -> None:
        """Append a single edge."""
        self.append([edge])

    def has_edge(self, src: str, dst: str) -> bool:
        """Check if an edge already exists (in either direction)."""
        for e in self.load_all():
            if (e.src == src and e.dst == dst) or (e.src == dst and e.dst == src):
                return True
        return False

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def rewrite(self, edges: list[Edge]) -> None:
        """Overwrite graph.jsonl with a new complete edge list."""
        with open(self._path, "w", encoding="utf-8") as f:
            for edge in edges:
                f.write(json.dumps(edge.to_dict(), ensure_ascii=False) + "\n")

    def remove_events(self, event_ids: set[str]) -> int:
        """Remove all edges involving any of the given event IDs.

        Returns count of edges removed.
        """
        all_edges = self.load_all()
        kept = [e for e in all_edges if e.src not in event_ids and e.dst not in event_ids]
        removed = len(all_edges) - len(kept)
        if removed > 0:
            self.rewrite(kept)
        return removed

    def edge_count(self) -> int:
        """Return total number of edges."""
        if not self._path.exists():
            return 0
        return sum(1 for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip())

    # ------------------------------------------------------------------
    # Migration helper
    # ------------------------------------------------------------------

    @staticmethod
    def migrate_from_events(events: list, path: Path) -> "GraphStore":
        """One-time migration: extract links from event frontmatter into graph.jsonl.

        Each event.links entry {id, type, weight} becomes a directed edge
        from event → target.  Deduplicates bidirectional pairs.
        """
        gs = GraphStore(path)
        seen: set[tuple[str, str]] = set()
        edges: list[Edge] = []

        for ev in events:
            for link in getattr(ev, "links", []):
                if not isinstance(link, dict):
                    continue
                dst = link.get("id", "")
                if not dst:
                    continue
                pair = tuple(sorted((ev.event_id, dst)))
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append(Edge(
                    src=ev.event_id,
                    dst=dst,
                    type=link.get("type", "temporal"),
                    weight=float(link.get("weight", 0.5)),
                ))

        gs.rewrite(edges)
        return gs
