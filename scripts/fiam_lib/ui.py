"""Rich colour palette, animated text, daemon animations."""

from __future__ import annotations

import time

from rich.console import Console as _Console
from rich.text import Text as _Text
from rich.live import Live as _Live

_console = _Console(highlight=False)

# Build a smooth 40-stop gradient by linearly interpolating between key hues.
# Each stop is (R, G, B) in 0-255.  We cycle: purple→blue→pink→yellow→mint→purple
def _lerp_hex(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"

_KEY_HUES = ["#b57bee", "#7eb8f7", "#f7a8d0", "#f7e08a", "#a8f0e8", "#b57bee"]
_STEPS_PER_SEGMENT = 8

_PAL: list[str] = []
for _i in range(len(_KEY_HUES) - 1):
    for _s in range(_STEPS_PER_SEGMENT):
        _PAL.append(_lerp_hex(_KEY_HUES[_i], _KEY_HUES[_i + 1], _s / _STEPS_PER_SEGMENT))
_PAL_LEN = len(_PAL)  # 40


def _flow(text: str, offset: int = 0, bold: bool = True) -> _Text:
    """Colour each non-space character at a different palette position.

    Incrementing `offset` by 1 per frame shifts the whole gradient left by one
    stop — at 5 fps that produces a smooth sweeping rainbow.
    """
    t = _Text()
    ci = 0
    for ch in text:
        if ch == " ":
            t.append(" ")
        else:
            col = _PAL[(ci + offset) % _PAL_LEN]
            style = f"bold {col}" if bold else col
            t.append(ch, style=style)
            ci += 1
    return t


# ------------------------------------------------------------------
# Conjuration animation
# ------------------------------------------------------------------

def _conjure() -> None:
    """Play the Latin-progression loading animation during setup.

    fio (I become) → fiam (I will become) → fiat (let it be done)
    → fiat lux ✦

    Each word sweeps the full palette so colours keep flowing as text grows.
    """
    words = ["fio", "fiam", "fiat", "fiat lux ✦"]
    # smoothly rotate through palette within each word display (~12 frames/word)
    frames_per_word = 12
    step = 0.22 / frames_per_word
    frame = 0
    with _Live("", refresh_per_second=30, console=_console, transient=False) as live:
        for word in words:
            for _ in range(frames_per_word):
                live.update(_flow(f"  {word:<22}", frame))
                frame += 1
                time.sleep(step)
    _console.print()


# ------------------------------------------------------------------
# Daemon animation
# ------------------------------------------------------------------

_ANIM_IDLE = [
    "       z   ▄████▄ \n        z ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  ",
    "      z    ▄████▄ \n       z  ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  ",
    "     z     ▄████▄ \n      z   ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  ",
    "    Z      ▄████▄ \n     z    ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  ",
]
_ANIM_ACTIVE = [
    "           ▄████▄ \n      !   ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  ",
    "           ▄████▄ \n          ████████ \n    ▄▄▄███▀▀██▀██\n   ▀▀▀▀██████████\n        ████████ \n         ██  ██  ",
]


def animate_while(func, frames: list[str]) -> any:
    """Run `func` in a separate thread while showing the animation frames.
    
    Returns the result of `func` or raises its exception.
    """
    import threading
    res = []
    err = []
    
    def _worker():
        try:
            res.append(func())
        except Exception as e:
            err.append(e)
            
    t = threading.Thread(target=_worker)
    t.start()
    
    # Sleep pseudo-infinitely until thread exits
    _animated_sleep(999999, frames, stop_check=lambda: not t.is_alive())
    
    t.join()
    if err:
        raise err[0]
    return res[0]

def _animated_sleep(seconds: float, frames: list[str], stop_check=None) -> None:
    """Animate flowing colours over multi-line text for `seconds`."""
    step = 0.125
    n = max(1, int(seconds / step))
    with _Live("", refresh_per_second=10, console=_console, transient=True) as live:
        for i in range(n):
            if stop_check and stop_check():
                break
            
            frame_lines = frames[i % len(frames)].split("\n")
            
            # Reconstruct with proper vertical centering, padding with spaces so we don't mess up rendering
            out_lines = []
            for line in frame_lines:
                out_lines.append(_flow(f"  {line}", i))
            
            # Combine into single Rich text object
            t = out_lines[0]
            for ln in out_lines[1:]:
                t.append("\n")
                t.append(ln)
                
            live.update(t)
            time.sleep(step)
