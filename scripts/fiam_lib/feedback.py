"""fiam feedback — interactive event rating TUI."""

from __future__ import annotations

import argparse
import sys

from fiam_lib.core import _build_config


# Weight adjustments — user_weight directly scales retrieval score
_WEIGHT_BOOST = 0.1     # 👍 per press
_WEIGHT_PENALTY = -0.1  # 👎 per press
_WEIGHT_MIN = 0.2
_WEIGHT_MAX = 2.0


def cmd_feedback(args: argparse.Namespace) -> None:
    """Interactive TUI: rate the most recent events with ←/→ keys."""
    from fiam.store.home import HomeStore

    config = _build_config(args)
    store = HomeStore(config)
    events = store.all_events()

    if not events:
        print("  No events to review.")
        return

    count = getattr(args, "count", 8) or 8
    # Most recent first
    recent = list(reversed(events))[:count]

    try:
        _run_tui(recent, store, config)
    except KeyboardInterrupt:
        print("\n  Cancelled.")


def _run_tui(events: list, store, config) -> None:
    """Rich-based TUI for event rating."""
    import re
    from datetime import datetime, timezone
    from rich.console import Console
    from rich.text import Text
    from rich.panel import Panel
    from rich.live import Live
    from rich.layout import Layout
    from rich.table import Table

    console = Console()
    now = datetime.now(timezone.utc)
    cursor = 0
    # Track per-event delta during this session
    deltas = [0.0] * len(events)
    modified = set()

    def _age_str(ev) -> str:
        age = now - ev.time
        if age.days > 0:
            return f"{age.days}d"
        h = age.seconds // 3600
        if h > 0:
            return f"{h}h"
        return f"{age.seconds // 60}m"

    def _preview(body: str, width: int = 60) -> str:
        clean = re.sub(r'\[(?:user|assistant)\]\s*', '', body).strip()
        clean = clean.replace('\n', ' ')
        if len(clean) > width:
            return clean[:width - 3] + "..."
        return clean

    def _render() -> Table:
        table = Table(
            show_header=True, header_style="bold #b57bee",
            show_lines=False, expand=True, padding=(0, 1),
        )
        table.add_column("#", width=3, justify="right")
        table.add_column("Age", width=4)
        table.add_column("V/A", width=12)
        table.add_column("Wt", width=6)
        table.add_column("Δ", width=5)
        table.add_column("Event", ratio=1)

        for i, ev in enumerate(events):
            is_selected = (i == cursor)
            age = _age_str(ev)
            va = f"v={ev.valence:+.1f} a={ev.arousal:.1f}"
            new_wt = max(_WEIGHT_MIN, min(_WEIGHT_MAX, ev.user_weight + deltas[i]))
            wt_display = f"{new_wt:.1f}"

            # Show delta
            d = deltas[i]
            if d > 0:
                delta_str = f"[green]+{d:.2f}[/]"
            elif d < 0:
                delta_str = f"[red]{d:.2f}[/]"
            else:
                delta_str = ""

            preview = _preview(ev.body)

            # Highlight selected row
            style = "bold #f7e08a" if is_selected else ""
            marker = "→" if is_selected else " "

            table.add_row(
                f"{marker}{i+1}",
                age,
                va,
                wt_display,
                delta_str,
                preview,
                style=style,
            )

        return table

    def _render_panel():
        table = _render()
        instructions = Text()
        instructions.append("  ↑↓", style="bold #7eb8f7")
        instructions.append(" navigate  ", style="dim")
        instructions.append("←", style="bold red")
        instructions.append(" weaken  ", style="dim")
        instructions.append("→", style="bold green")
        instructions.append(" strengthen  ", style="dim")
        instructions.append("Enter", style="bold #f7a8d0")
        instructions.append(" save & exit", style="dim")

        layout = Layout()
        layout.split_column(
            Layout(table, name="table"),
            Layout(instructions, name="help", size=1),
        )
        return Panel(layout, title="[bold #b57bee]fiam feedback[/]", border_style="#b57bee")

    # Use msvcrt on Windows, tty on Unix
    if sys.platform == "win32":
        import msvcrt

        def _getkey() -> str:
            """Read a keypress on Windows. Returns 'up', 'down', 'left', 'right', 'enter', or char."""
            ch = msvcrt.getwch()
            if ch == '\r':
                return 'enter'
            if ch in ('\x00', '\xe0'):
                ch2 = msvcrt.getwch()
                return {'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right'}.get(ch2, '')
            return ch
    else:
        import tty
        import termios

        def _getkey() -> str:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == '\r' or ch == '\n':
                    return 'enter'
                if ch == '\x1b':
                    ch2 = sys.stdin.read(1)
                    if ch2 == '[':
                        ch3 = sys.stdin.read(1)
                        return {'A': 'up', 'B': 'down', 'C': 'right', 'D': 'left'}.get(ch3, '')
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    with Live(_render_panel(), console=console, refresh_per_second=10, screen=False) as live:
        while True:
            key = _getkey()

            if key == 'enter':
                break
            elif key == 'up':
                cursor = max(0, cursor - 1)
            elif key == 'down':
                cursor = min(len(events) - 1, cursor + 1)
            elif key == 'right':
                # Strengthen
                new_total = events[cursor].user_weight + deltas[cursor] + _WEIGHT_BOOST
                if new_total <= _WEIGHT_MAX:
                    deltas[cursor] += _WEIGHT_BOOST
                    modified.add(cursor)
            elif key == 'left':
                # Weaken
                new_total = events[cursor].user_weight + deltas[cursor] + _WEIGHT_PENALTY
                if new_total >= _WEIGHT_MIN:
                    deltas[cursor] += _WEIGHT_PENALTY
                    modified.add(cursor)
            elif key == 'q':
                break

            live.update(_render_panel())

    # Apply changes
    applied = 0
    for i in modified:
        if deltas[i] != 0:
            ev = events[i]
            ev.user_weight = max(_WEIGHT_MIN, min(_WEIGHT_MAX, ev.user_weight + deltas[i]))
            store.update_metadata(ev)
            applied += 1
    # Silently collect training data for future personalized model
    _log_feedback_training(events, deltas, config.code_path)
    console.print()
    if applied:
        console.print(f"  ✓ Updated {applied} event(s)")
    else:
        console.print("  (no changes)")
    console.print()


def _log_feedback_training(events: list, deltas: list[float], code_path) -> None:
    """Silently log all reviewed events as a cohort training signal."""
    from datetime import datetime, timezone
    from fiam.store.training import log_feedback_cohort

    now = datetime.now(timezone.utc)
    candidates = []
    has_labels = False

    for i, ev in enumerate(events):
        d = deltas[i]
        label = 1 if d > 0 else (-1 if d < 0 else 0)
        if label != 0:
            has_labels = True

        age_hours = (now - ev.time).total_seconds() / 3600
        candidates.append({
            "event_id": ev.event_id,
            "label": label,
            "event_arousal": round(ev.arousal, 4),
            "event_valence": round(ev.valence, 4),
            "event_age_hours": round(age_hours, 2),
            "user_weight": round(ev.user_weight, 4),
        })

    if has_labels:
        log_feedback_cohort(
            code_path, 
            trigger_context="tui_recent_events", 
            candidates=candidates
        )
