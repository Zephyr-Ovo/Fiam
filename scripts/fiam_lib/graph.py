"""Obsidian wikilink graph generation."""

from __future__ import annotations

import argparse
import re

from fiam_lib.core import _build_config


# Word extraction patterns — variable length, maximum chaos
_GRAPH_CJK_WORD = re.compile(r"[\u4e00-\u9fff]{1,7}")   # 1–7 CJK chars
_GRAPH_EN_WORD  = re.compile(r"\b[a-zA-Z]{3,7}\b")       # 3–7 letter words (punchy)
_GRAPH_BORING_EN = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "her", "was", "one", "our", "out", "day", "get", "has", "him",
    "his", "how", "its", "let", "man", "new", "now", "old", "see",
    "two", "way", "who", "boy", "did", "etc", "use",
    "this", "that", "with", "from", "have", "been", "will", "what",
    "when", "just", "your", "they", "them", "then", "than", "some",
    "were", "each", "more", "also", "like", "into", "very", "much",
    "here", "there", "would", "could", "should", "about", "which",
    "their", "other", "after", "before", "because", "through",
    "these", "those", "being", "doing", "having", "started", "ended",
    "user", "assistant",
}
_GRAPH_BORING_ZH = {
    "的是", "不是", "可以", "我们", "你们", "他们", "这个", "那个",
    "什么", "怎么", "的", "了", "是", "在", "我", "你", "他", "她",
    "它", "也", "都", "不", "没", "有", "就", "和", "与", "或",
}


def _graph_candidates(body: str) -> list[str]:
    """Return a shuffled list of interesting word candidates from event body."""
    import random
    clean = re.sub(r'\[(?:user|assistant)\]\s*', '', body).strip()

    zh = [w for w in _GRAPH_CJK_WORD.findall(clean) if w not in _GRAPH_BORING_ZH]
    en = [w for w in _GRAPH_EN_WORD.findall(clean) if w.lower() not in _GRAPH_BORING_EN]

    # Deduplicate while preserving order, then shuffle for chaos
    seen: set[str] = set()
    candidates: list[str] = []
    for w in zh + en:
        if w not in seen:
            seen.add(w)
            candidates.append(w)

    random.shuffle(candidates)
    return candidates


def _pick_name(body: str, used: set[str]) -> str:
    """Pick an unused name from this event's body; re-roll on collision.

    If every candidate in the body is already used, fall back to
    appending a number to the first good candidate.
    """
    candidates = _graph_candidates(body)

    for c in candidates:
        if c not in used:
            return c

    # All candidates taken — number-suffix the first one
    base = candidates[0] if candidates else "memory"
    i = 2
    while f"{base}{i}" in used:
        i += 1
    return f"{base}{i}"


def cmd_graph(args: argparse.Namespace) -> None:
    """Generate an Obsidian graph from the event store.

    Copies events to store/graph/ with human-readable names,
    adds [[wikilinks]] between events whose cosine similarity
    exceeds the threshold. Open the folder in Obsidian → graph view!
    """
    import shutil
    import numpy as np
    from fiam.store.home import HomeStore

    config = _build_config(args)
    store = HomeStore(config)
    events = store.all_events()

    if not events:
        print("No events in store. Run 'fiam post' first.")
        return

    threshold = getattr(args, "threshold", 0.75)

    # --- Phase 1: Name assignment ---
    used_names: set[str] = set()
    name_map: dict[str, str] = {}  # event_id → display name

    for ev in events:
        name = _pick_name(ev.body, used_names)
        used_names.add(name)
        name_map[ev.filename] = name

    # --- Phase 2: Copy files ---
    graph_dir = config.store_dir / "graph"
    if graph_dir.exists():
        shutil.rmtree(graph_dir)
    graph_dir.mkdir(parents=True)

    # Write clean Obsidian-friendly files
    for ev in events:
        name = name_map[ev.filename]
        # Strip role markers for cleaner display
        clean_body = re.sub(r'\[(?:user|assistant)\]\s*', '', ev.body).strip()

        lines = [
            f"*{ev.time.strftime('%Y-%m-%d %H:%M')}*",
            f"v={ev.valence:+.2f}  a={ev.arousal:.2f}",
            "",
            clean_body,
            "",
        ]
        (graph_dir / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")

    # --- Phase 3: Compute similarity & add wikilinks ---
    # Load embeddings
    vecs: dict[str, np.ndarray] = {}
    for ev in events:
        if ev.embedding:
            npy_path = config.store_dir / ev.embedding
            if npy_path.exists():
                vecs[ev.filename] = np.load(npy_path).astype(np.float32).flatten()

    # Pairwise cosine similarity → wikilinks
    links: dict[str, list[str]] = {ev.filename: [] for ev in events}
    ev_ids = [ev.filename for ev in events if ev.filename in vecs]
    link_count = 0

    for i, a in enumerate(ev_ids):
        for b in ev_ids[i + 1:]:
            va, vb = vecs[a], vecs[b]
            sim = float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))
            if sim >= threshold:
                links[a].append(b)
                links[b].append(a)
                link_count += 1

    # Append wikilinks to files
    for ev in events:
        neighbors = links.get(ev.filename, [])
        if not neighbors:
            continue
        name = name_map[ev.filename]
        path = graph_dir / f"{name}.md"
        wikilinks = " ".join(f"[[{name_map[n]}]]" for n in neighbors)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"---\n{wikilinks}\n")

    # --- Done! ---
    print()
    print(f"  🎉 fiam 1.0 — memory graph")
    print(f"  {len(events)} events → {graph_dir}")
    print(f"  {link_count} links (threshold ≥ {threshold})")
    print()
    print(f"  Open in Obsidian: {graph_dir}")
    print()

    # Preview
    for ev in events:
        name = name_map[ev.filename]
        n_links = len(links.get(ev.filename, []))
        link_str = f"  ({'·'.join(name_map[n] for n in links[ev.filename])})" if n_links else ""
        print(f"    {name}{link_str}")

    print()
