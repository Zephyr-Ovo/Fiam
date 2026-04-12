"""
Debug HTML visualization for MemoryGraph.

Produces a self-contained HTML file with a force-directed graph
using basic SVG + JS (no external deps). Not a product — just for debug.
"""

from __future__ import annotations

import json
from pathlib import Path

from fiam.retriever.graph import MemoryGraph

_TEMPLATE = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>fiam memory graph</title>
<style>
  body { margin: 0; background: #1a1a2e; font-family: monospace; color: #eee; }
  svg { width: 100vw; height: 100vh; }
  .node circle { stroke: #fff; stroke-width: 1.5; }
  .node text { font-size: 9px; fill: #ccc; pointer-events: none; }
  line.temporal { stroke: #4a9eff; }
  line.semantic { stroke: #ff6b6b; }
  line.causal   { stroke: #51cf66; }
  line { stroke-opacity: 0.4; }
  #info { position: fixed; top: 10px; left: 10px; font-size: 12px; opacity: 0.7; }
</style>
</head><body>
<div id="info">nodes: NCOUNT | edges: ECOUNT | drag to explore</div>
<svg id="canvas"></svg>
<script>
const data = GRAPH_DATA;
const svg = document.getElementById("canvas");
const W = window.innerWidth, H = window.innerHeight;
svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

// Init positions
data.nodes.forEach((n, i) => {
  n.x = W/2 + (Math.random()-0.5)*400;
  n.y = H/2 + (Math.random()-0.5)*400;
  n.vx = 0; n.vy = 0;
});
const nodeMap = {};
data.nodes.forEach(n => nodeMap[n.id] = n);

// Draw edges
data.edges.forEach(e => {
  const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
  line.classList.add(e.type || "temporal");
  line.setAttribute("stroke-width", Math.max(0.5, e.weight * 3));
  e._el = line;
  svg.appendChild(line);
});

// Draw nodes
data.nodes.forEach(n => {
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.classList.add("node");
  const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  c.setAttribute("r", 5);
  c.setAttribute("fill", "#4a9eff");
  const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
  t.setAttribute("dx", 8); t.setAttribute("dy", 3);
  t.textContent = n.id.replace(/^ev_/, "");
  g.appendChild(c); g.appendChild(t);
  n._el = g;

  // Drag
  let dragging = false;
  c.addEventListener("mousedown", ev => { dragging = true; ev.preventDefault(); });
  document.addEventListener("mousemove", ev => {
    if (!dragging) return;
    n.x = ev.clientX; n.y = ev.clientY; n.vx = 0; n.vy = 0;
  });
  document.addEventListener("mouseup", () => dragging = false);

  svg.appendChild(g);
});

// Simple force simulation
function tick() {
  // Repulsion
  for (let i = 0; i < data.nodes.length; i++) {
    for (let j = i+1; j < data.nodes.length; j++) {
      const a = data.nodes[i], b = data.nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx*dx + dy*dy + 1;
      let f = 800 / d2;
      a.vx += dx*f; a.vy += dy*f;
      b.vx -= dx*f; b.vy -= dy*f;
    }
  }
  // Attraction (edges)
  data.edges.forEach(e => {
    const s = nodeMap[e.src], t = nodeMap[e.dst];
    if (!s || !t) return;
    let dx = t.x - s.x, dy = t.y - s.y;
    let d = Math.sqrt(dx*dx + dy*dy + 1);
    let f = (d - 80) * 0.005 * (e.weight || 0.5);
    s.vx += dx*f; s.vy += dy*f;
    t.vx -= dx*f; t.vy -= dy*f;
  });
  // Center gravity
  data.nodes.forEach(n => {
    n.vx += (W/2 - n.x) * 0.001;
    n.vy += (H/2 - n.y) * 0.001;
    n.vx *= 0.9; n.vy *= 0.9;
    n.x += n.vx; n.y += n.vy;
    n._el.setAttribute("transform", `translate(${n.x},${n.y})`);
  });
  data.edges.forEach(e => {
    const s = nodeMap[e.src], t = nodeMap[e.dst];
    if (!s || !t) return;
    e._el.setAttribute("x1", s.x); e._el.setAttribute("y1", s.y);
    e._el.setAttribute("x2", t.x); e._el.setAttribute("y2", t.y);
  });
  requestAnimationFrame(tick);
}
tick();
</script></body></html>
"""


def render_html(graph: MemoryGraph, output_path: Path | str) -> Path:
    """Write a self-contained HTML debug visualization of the graph."""
    output_path = Path(output_path)
    data = graph.to_debug_dict()
    html = _TEMPLATE.replace("GRAPH_DATA", json.dumps(data, default=str))
    html = html.replace("NCOUNT", str(graph.node_count))
    html = html.replace("ECOUNT", str(graph.edge_count))
    output_path.write_text(html, encoding="utf-8")
    return output_path
