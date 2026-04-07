"""fiam rem — consolidate similar events via interactive TUI + LLM."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from fiam_lib.core import _build_config


# Clustering threshold — cosine similarity above this = candidates for merge
_CLUSTER_THRESHOLD = 0.82
_MIN_CLUSTER_SIZE = 2


def cmd_rem(args: argparse.Namespace) -> None:
    """Interactive TUI: review clusters of similar events and consolidate."""
    config = _build_config(args)

    if not config.narrative_llm_enabled:
        print("  ✗ rem requires LLM. Enable with: fiam settings --set llm_enabled=true")
        return

    from fiam.store.home import HomeStore

    store = HomeStore(config)
    events = store.all_events()

    if len(events) < _MIN_CLUSTER_SIZE:
        print(f"  Need at least {_MIN_CLUSTER_SIZE} events for consolidation.")
        return

    print("  Loading embeddings...")
    threshold = getattr(args, "threshold", _CLUSTER_THRESHOLD) or _CLUSTER_THRESHOLD
    clusters = _find_clusters(events, config, threshold=threshold)

    if not clusters:
        print("  No similar-enough clusters found.")
        return

    print(f"  Found {len(clusters)} cluster(s) to review.\n")

    try:
        _run_tui(clusters, config, store)
    except KeyboardInterrupt:
        print("\n  Cancelled.")


# ------------------------------------------------------------------
# Clustering via pairwise cosine
# ------------------------------------------------------------------

def _load_vec(event, config):
    """Load embedding vector for an event."""
    import numpy as np

    if not event.embedding:
        return None
    vec_path = config.code_path / event.embedding
    if not vec_path.exists():
        return None
    return np.load(vec_path).astype(np.float32)


def _find_clusters(events: list, config, *, threshold: float = _CLUSTER_THRESHOLD) -> list[list]:
    """Find groups of events with high pairwise cosine similarity.

    Uses greedy single-linkage: walk events, attach to existing cluster
    if similarity to any member exceeds threshold.
    """
    import numpy as np

    # Load all vectors
    vecs: list[tuple[int, np.ndarray]] = []
    for i, ev in enumerate(events):
        v = _load_vec(ev, config)
        if v is not None:
            vecs.append((i, v))

    if len(vecs) < 2:
        return []

    # Build adjacency via cosine similarity
    clusters: list[set[int]] = []
    assigned: set[int] = set()

    for i in range(len(vecs)):
        if vecs[i][0] in assigned:
            continue
        cluster = {vecs[i][0]}
        for j in range(i + 1, len(vecs)):
            if vecs[j][0] in assigned:
                continue
            sim = float(np.dot(vecs[i][1], vecs[j][1]) / (
                np.linalg.norm(vecs[i][1]) * np.linalg.norm(vecs[j][1]) + 1e-9
            ))
            if sim >= threshold:
                cluster.add(vecs[j][0])

        if len(cluster) >= _MIN_CLUSTER_SIZE:
            clusters.append(cluster)
            assigned.update(cluster)

    # Convert to event lists (sorted by time)
    result = []
    for cluster_set in clusters:
        group = sorted([events[i] for i in cluster_set], key=lambda e: e.time)
        result.append(group)

    # Sort clusters by earliest event time
    result.sort(key=lambda g: g[0].time)
    return result


# ------------------------------------------------------------------
# LLM consolidation
# ------------------------------------------------------------------

def _consolidate_via_llm(events: list, config) -> str:
    """Call LLM to summarise a cluster of events into one consolidated memory."""
    fragments = []
    for ev in events:
        age_str = ev.time.strftime("%Y-%m-%d %H:%M")
        body_preview = ev.body.strip()
        if len(body_preview) > 500:
            body_preview = body_preview[:497] + "..."
        fragments.append(f"[{age_str}]\n{body_preview}")

    prompt = (
        "You are consolidating similar memory fragments into one concise memory.\n"
        "Below are related memory fragments from different times. "
        "Combine them into a single, coherent memory that preserves the key information, "
        "emotional undertones, and important details. Write in the same language as the fragments.\n"
        "Output ONLY the consolidated memory text, no preamble.\n\n"
        + "\n---\n".join(fragments)
    )

    api_key = ""
    if config.narrative_llm_api_key_env:
        api_key = os.environ.get(config.narrative_llm_api_key_env, "")

    if config.narrative_llm_provider == "anthropic":
        return _call_anthropic(prompt, config, api_key)
    else:
        return _call_openai_compat(prompt, config, api_key)


def _call_anthropic(prompt: str, config, api_key: str) -> str:
    import anthropic

    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key

    client = anthropic.Anthropic(**kwargs)
    msg = client.messages.create(
        model=config.narrative_llm_model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _call_openai_compat(prompt: str, config, api_key: str) -> str:
    import httpx

    base_url = config.narrative_llm_base_url or "https://api.openai.com/v1"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={
            "model": config.narrative_llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ------------------------------------------------------------------
# Interactive TUI
# ------------------------------------------------------------------

def _run_tui(clusters: list[list], config, store) -> None:
    """Step through clusters: review, consolidate, confirm."""
    import re
    from datetime import datetime, timezone

    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console()
    total = len(clusters)
    merged_count = 0
    skipped_count = 0

    for ci, cluster in enumerate(clusters):
        # --- Render cluster info ---
        console.print()
        console.print(Panel(
            _render_cluster(cluster),
            title=f"[bold #b57bee]Cluster {ci + 1}/{total}  ·  {len(cluster)} events[/]",
            border_style="#b57bee",
        ))

        # --- Interactive choices ---
        console.print(
            "  [bold green]Enter[/] consolidate  "
            "[bold red]s[/] skip  "
            "[bold yellow]q[/] quit"
        )

        while True:
            key = _getch()
            if key in ('\r', '\n', 'enter'):
                # Consolidate this cluster
                console.print("  [dim]Calling LLM...[/dim]", end="")
                try:
                    summary = _consolidate_via_llm(cluster, config)
                except Exception as e:
                    console.print(f"\n  [red]Error: {e}[/]")
                    break

                console.print(" done.")
                console.print()
                console.print(Panel(summary, title="[bold #f7e08a]Consolidated[/]",
                                    border_style="#f7e08a", width=80))

                # Confirm / regenerate / cancel
                console.print(
                    "  [bold green]Enter[/] accept  "
                    "[bold #7eb8f7]r[/] regenerate  "
                    "[bold red]s[/] discard"
                )

                while True:
                    key2 = _getch()
                    if key2 in ('\r', '\n', 'enter'):
                        # Accept: create merged event, archive originals
                        _apply_merge(cluster, summary, config, store, console)
                        merged_count += 1
                        break
                    elif key2 == 'r':
                        # Regenerate
                        console.print("  [dim]Regenerating...[/dim]", end="")
                        try:
                            summary = _consolidate_via_llm(cluster, config)
                        except Exception as e:
                            console.print(f"\n  [red]Error: {e}[/]")
                            break
                        console.print(" done.")
                        console.print()
                        console.print(Panel(summary, title="[bold #f7e08a]Consolidated[/]",
                                            border_style="#f7e08a", width=80))
                        console.print(
                            "  [bold green]Enter[/] accept  "
                            "[bold #7eb8f7]r[/] regenerate  "
                            "[bold red]s[/] discard"
                        )
                        continue
                    elif key2 == 's':
                        console.print("  [dim]Discarded.[/dim]")
                        skipped_count += 1
                        break
                break
            elif key == 's':
                console.print("  [dim]Skipped.[/dim]")
                skipped_count += 1
                break
            elif key == 'q':
                console.print()
                console.print(f"  [bold]Done.[/] Merged: {merged_count}, Skipped: {skipped_count}")
                return

    console.print()
    console.print(f"  [bold]All clusters reviewed.[/] Merged: {merged_count}, Skipped: {skipped_count}")


def _render_cluster(cluster: list) -> str:
    """Render cluster events as a readable list."""
    import re
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    lines = []

    for ev in cluster:
        age = now - ev.time
        if age.days > 0:
            time_str = f"{age.days}d ago"
        elif age.seconds > 3600:
            time_str = f"{age.seconds // 3600}h ago"
        else:
            time_str = f"{age.seconds // 60}m ago"

        body_clean = re.sub(r'\[(?:user|assistant)\]\s*', '', ev.body).strip()
        body_clean = body_clean.replace('\n', ' ')
        if len(body_clean) > 100:
            body_clean = body_clean[:97] + "..."

        va = f"v={ev.valence:+.1f} a={ev.arousal:.1f}"
        lines.append(f"  [{ev.filename}] ({time_str}, {va}) {body_clean}")

    return "\n".join(lines)


def _apply_merge(cluster: list, summary: str, config, store, console) -> None:
    """Create merged event, archive originals."""
    import numpy as np
    from datetime import datetime, timezone

    # Use the most recent event's embedding for the merged event
    best_ev = max(cluster, key=lambda e: e.time)

    # Create new event ID
    new_id = store.new_event_id()

    # Average emotion values
    avg_valence = sum(e.valence for e in cluster) / len(cluster)
    avg_arousal = sum(e.arousal for e in cluster) / len(cluster)
    avg_confidence = sum(e.confidence for e in cluster) / len(cluster)

    # Highest user_weight from the cluster
    max_weight = max(e.user_weight for e in cluster)

    # Copy embedding from best event
    embedding_rel = ""
    if best_ev.embedding:
        src_path = config.code_path / best_ev.embedding
        if src_path.exists():
            dst_name = f"embeddings/{new_id}.npy"
            dst_path = config.code_path / "store" / dst_name
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            embedding_rel = dst_name

    from fiam.store.formats import EventRecord

    merged = EventRecord(
        filename=new_id,
        time=datetime.now(timezone.utc),
        valence=avg_valence,
        arousal=avg_arousal,
        confidence=avg_confidence,
        strength=max(e.strength for e in cluster),
        user_weight=max_weight,
        embedding=embedding_rel,
        embedding_dim=best_ev.embedding_dim,
        tags=list({t for e in cluster for t in e.tags}),
        links=[],  # fresh links; next reindex will rebuild
        body=summary,
    )
    store.write_event(merged)

    # Archive originals: move to store/events/_archived/
    archive_dir = config.events_dir / "_archived"
    archive_dir.mkdir(exist_ok=True)
    for ev in cluster:
        src = config.events_dir / f"{ev.filename}.md"
        if src.exists():
            shutil.move(str(src), str(archive_dir / src.name))

    console.print(f"  [bold #a8f0e8]✓[/] {new_id} ← merged {len(cluster)} events (originals archived)")


# ------------------------------------------------------------------
# Cross-platform single keypress
# ------------------------------------------------------------------

def _getch() -> str:
    """Read a single keypress."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch == '\r':
            return 'enter'
        return ch
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch in ('\r', '\n'):
                return 'enter'
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
