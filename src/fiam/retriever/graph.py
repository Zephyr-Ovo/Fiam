"""
MemoryGraph — NetworkX-backed graph for spreading activation retrieval.

Implements three SYNAPSE mechanisms:
  1. Spreading activation: seed nodes fire, energy propagates along edges
  2. Lateral inhibition: simultaneously active nodes of same-type suppress
     each other (keeps results diverse)
  3. Temporal edge decay: edge weights decay toward 0 over time

The graph is rebuilt from store/graph.jsonl on each pre_session (cheap at <10k events).
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp, log

import networkx as nx

from fiam.config import FiamConfig
from fiam.store.formats import EventRecord


class MemoryGraph:
    """In-memory directed graph over events."""

    # Default edge-type multipliers (fallback when no config is supplied).
    # Kept as a class attribute so pipeline edge_typer can still `setdefault()`
    # on newly-discovered types without needing the config.
    _TYPE_MULT: dict[str, float] = {
        "causal":      1.4,
        "cause":       1.4,
        "remind":      1.2,
        "elaboration": 1.0,
        "semantic":    0.8,
        "temporal":    0.5,
        "contrast":    0.3,
    }

    def __init__(self, decay_half_life_days: float = 30.0,
                 config: FiamConfig | None = None) -> None:
        self.G: nx.DiGraph = nx.DiGraph()
        if config is not None:
            self._decay_half_life = config.graph_edge_decay_half_life
            self._type_mult = dict(config.graph_edge_type_weights)
            self._spread_steps = config.graph_spread_steps
            self._spread_decay = config.graph_spread_decay
            self._inhibition = config.graph_inhibition_factor
        else:
            self._decay_half_life = decay_half_life_days
            self._type_mult = dict(self._TYPE_MULT)
            self._spread_steps = 2
            self._spread_decay = 0.5
            self._inhibition = 0.3

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def build(self, events: list[EventRecord], now: datetime | None = None,
              edges: list[dict] | None = None) -> None:
        """Populate graph from event records and an external edge list.

        Args:
            events: All events (used to register nodes).
            now: Reference time for edge decay.
            edges: List of edge dicts from GraphStore.load_as_dicts().
                   Each: {"src", "dst", "type", "weight"}.
                   If None, falls back to event.links (legacy compat).
        """
        now = now or datetime.now(timezone.utc)
        self.G.clear()

        event_times: dict[str, datetime] = {}
        for ev in events:
            self.G.add_node(ev.event_id, time=ev.time, intensity=ev.intensity)
            event_times[ev.event_id] = ev.time

        # Determine edge source: graph.jsonl edges OR legacy event.links
        if edges is not None:
            self._add_edges_from_list(edges, event_times, now)
        else:
            self._add_edges_from_events(events, now)

    def _add_edges_from_list(self, edges: list[dict], event_times: dict[str, datetime],
                             now: datetime) -> None:
        """Add edges from graph.jsonl format."""
        for edge in edges:
            src = edge.get("src", "")
            dst = edge.get("dst", "")
            if not src or not dst or src not in self.G or dst not in self.G:
                continue
            raw_weight = edge.get("weight", 0.5)
            link_type = edge.get("type", "temporal")

            edge_time = event_times.get(src, now)
            age_days = (now - edge_time).total_seconds() / 86400.0
            decay = exp(-0.693 * age_days / self._decay_half_life)
            effective = raw_weight * decay

            if self.G.has_edge(src, dst):
                if effective <= self.G[src][dst].get("weight", 0.0):
                    continue

            self.G.add_edge(src, dst, weight=effective, type=link_type)

    def _add_edges_from_events(self, events: list[EventRecord], now: datetime) -> None:
        """Legacy: add edges from event.links fields."""
        for ev in events:
            for link in ev.links:
                if not isinstance(link, dict):
                    continue
                target = link.get("id", "")
                if not target or target not in self.G:
                    continue
                raw_weight = link.get("weight", 0.5)
                link_type = link.get("type", "temporal")

                edge_time = ev.time
                age_days = (now - edge_time).total_seconds() / 86400.0
                decay = exp(-0.693 * age_days / self._decay_half_life)
                effective = raw_weight * decay

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
        steps: int | None = None,
        decay_per_step: float | None = None,
        inhibition_factor: float | None = None,
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
        # Fall back to instance defaults (from config) if caller didn't override
        if steps is None:
            steps = self._spread_steps
        if decay_per_step is None:
            decay_per_step = self._spread_decay
        if inhibition_factor is None:
            inhibition_factor = self._inhibition

        activation: dict[str, float] = {}

        # Seed
        for eid, score in zip(seed_ids, seed_scores):
            if eid in self.G:
                activation[eid] = score

        # Propagate (fire-once: each node propagates only on the step it's first activated)
        fired: set[str] = set(activation.keys())  # seeds count as fired
        for step in range(steps):
            delta: dict[str, float] = {}
            for node, energy in activation.items():
                if energy <= 0.01:
                    continue
                # Fire-once: seeds fire on step 0, other nodes fire only once
                if step > 0 and node in fired:
                    continue
                for _, neighbor, data in self.G.out_edges(node, data=True):
                    w = data.get("weight", 0.5)
                    edge_type = data.get("type", "temporal")
                    type_mult = self._type_mult.get(edge_type, 0.5)
                    # Fan penalty: high-degree nodes dilute their energy
                    fan_out = max(1, self.G.out_degree(node))
                    fan_penalty = 1.0 / (1.0 + log(fan_out))
                    propagated = energy * w * type_mult * fan_penalty * decay_per_step
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

            # Mark all nodes that had energy this step as fired
            for node in list(activation.keys()):
                if activation[node] > 0.01:
                    fired.add(node)

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
