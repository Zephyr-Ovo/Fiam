"""
MemoryGraph — NetworkX-backed graph for spreading activation retrieval.

Implements three SYNAPSE mechanisms:
  1. Spreading activation: seed nodes fire, energy propagates along edges
  2. Lateral inhibition: simultaneously active nodes of same-type suppress
     each other (keeps results diverse)
  3. Temporal edge decay: edge weights decay toward 0 over time

The graph is rebuilt from event links on each pre_session (cheap at <10k events).
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp

import networkx as nx

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord


class MemoryGraph:
    """In-memory directed graph over events."""

    def __init__(self, decay_half_life_days: float = 30.0) -> None:
        self.G: nx.DiGraph = nx.DiGraph()
        self._decay_half_life = decay_half_life_days

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def build(self, events: list[EventRecord], now: datetime | None = None) -> None:
        """Populate graph from event records. Edges come from event.links."""
        now = now or datetime.now(timezone.utc)
        self.G.clear()

        for ev in events:
            self.G.add_node(ev.event_id, time=ev.time, arousal=ev.arousal)

        for ev in events:
            for link in ev.links:
                if not isinstance(link, dict):
                    continue
                target = link.get("id", "")
                if not target or target not in self.G:
                    continue
                raw_weight = link.get("weight", 0.5)
                link_type = link.get("type", "temporal")

                # Temporal decay: older edges weaken
                edge_time = ev.time  # approximate edge creation ≈ event time
                age_days = (now - edge_time).total_seconds() / 86400.0
                decay = exp(-0.693 * age_days / self._decay_half_life)
                effective = raw_weight * decay

                # If edge already exists (bidirectional links), keep the stronger one
                if self.G.has_edge(ev.event_id, target):
                    existing_w = self.G[ev.event_id][target].get("weight", 0.0)
                    if effective <= existing_w:
                        continue

                self.G.add_edge(
                    ev.event_id, target,
                    weight=effective,
                    type=link_type,
                )

    # ------------------------------------------------------------------
    # Spreading activation
    # ------------------------------------------------------------------

    def spread(
        self,
        seed_ids: list[str],
        seed_scores: list[float],
        *,
        steps: int = 2,
        decay_per_step: float = 0.5,
        inhibition_factor: float = 0.3,
    ) -> dict[str, float]:
        """Run spreading activation from seed nodes.

        Args:
            seed_ids: Event IDs to start activation from.
            seed_scores: Initial energy for each seed (e.g. base retrieval score).
            steps: Number of propagation hops.
            decay_per_step: Energy multiplier per hop (< 1 = attenuation).
            inhibition_factor: Fraction of energy subtracted from nodes
                activated by multiple competing sources (lateral inhibition).

        Returns:
            Dict mapping event_id → accumulated activation score.
        """
        activation: dict[str, float] = {}

        # Seed
        for eid, score in zip(seed_ids, seed_scores):
            if eid in self.G:
                activation[eid] = score

        # Propagate
        for step in range(steps):
            delta: dict[str, float] = {}
            for node, energy in activation.items():
                if energy <= 0.01:
                    continue
                for _, neighbor, data in self.G.out_edges(node, data=True):
                    w = data.get("weight", 0.5)
                    propagated = energy * w * decay_per_step
                    if propagated > 0.001:
                        delta[neighbor] = delta.get(neighbor, 0.0) + propagated

            # Lateral inhibition: if a node receives energy from multiple
            # sources, dampen the total (promotes diversity).
            for node, total in delta.items():
                source_count = sum(
                    1 for src in activation
                    if self.G.has_edge(src, node) and activation[src] > 0.01
                )
                if source_count > 1:
                    total *= (1.0 - inhibition_factor)
                activation[node] = activation.get(node, 0.0) + total

        # Normalise to [0, 1]
        if activation:
            max_a = max(activation.values())
            if max_a > 0:
                activation = {k: v / max_a for k, v in activation.items()}

        return activation

    # ------------------------------------------------------------------
    # Debug / visualisation helpers
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return self.G.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.G.number_of_edges()

    def subgraph_around(self, event_id: str, radius: int = 2) -> nx.DiGraph:
        """Return the ego subgraph around *event_id* up to *radius* hops."""
        if event_id not in self.G:
            return nx.DiGraph()
        nodes = nx.single_source_shortest_path_length(self.G, event_id, cutoff=radius)
        return self.G.subgraph(nodes.keys()).copy()

    def to_debug_dict(self) -> dict:
        """Lightweight JSON-safe snapshot for debugging."""
        nodes = []
        for nid, data in self.G.nodes(data=True):
            nodes.append({"id": nid, "time": data.get("time", "").isoformat() if hasattr(data.get("time", ""), "isoformat") else str(data.get("time", ""))})
        edges = []
        for u, v, data in self.G.edges(data=True):
            edges.append({
                "src": u, "dst": v,
                "weight": round(data.get("weight", 0), 4),
                "type": data.get("type", ""),
            })
        return {"nodes": nodes, "edges": edges}
