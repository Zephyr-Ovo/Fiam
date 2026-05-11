"""One-shot migration of flow.jsonl from the old beat schema to the new one.

Old: {t, actor, channel, text, runtime?, user_status?, ai_status?, meta?}
     channel could be: favilla / browser / cc / system / think / action
     runtime could be: cc / api / browser / claude / gemini / None

New: {t, actor, channel, kind, content, runtime?, meta?}
     channel ∈ surface (favilla, browser, stroll, email, studio, cc, system, ...)
     kind    ∈ {message, action, tool_result, think, schedule}
     runtime ∈ {cc, claude, gemini, ...} (no "api", no "browser")

Usage: python scripts/migrate_flow_schema.py path/to/flow.jsonl [--dry-run]
A backup .bak is written next to the input.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _infer_runtime_family(model: str | None) -> str | None:
    if not model:
        return None
    s = model.lower()
    if "gemini" in s:
        return "gemini"
    if "claude" in s:
        return "claude"
    if "gpt" in s or "openai" in s:
        return "gpt"
    if "/" in s:
        return s.split("/", 1)[0]
    return s or None


def migrate_line(d: dict) -> dict | None:
    """Return the migrated beat dict, or None to drop the line."""
    # If already new-shape, leave alone.
    if "content" in d and "kind" in d:
        return d

    actor = d.get("actor", "system")
    old_channel = d.get("channel", "")
    text = d.get("text", d.get("content", ""))
    old_runtime = d.get("runtime")
    meta = dict(d.get("meta") or {})

    # Default mapping
    new_channel = old_channel
    kind = "message"
    runtime = old_runtime

    if old_channel == "think":
        # Belongs on the surface that produced it. Old code stored think under "think".
        # Best guess: cc thoughts came from cc runtime; otherwise treat as favilla.
        new_channel = "cc" if old_runtime == "cc" else "favilla"
        kind = "think"
        if "source" not in meta:
            meta["source"] = "native" if old_runtime == "cc" else "marker"
    elif old_channel == "action":
        # Tool action — surface from runtime tag
        if old_runtime == "browser":
            new_channel = "browser"
            runtime = None  # browser is channel, not runtime
        elif old_runtime == "cc":
            new_channel = "cc"
        else:
            new_channel = old_channel or "system"
        kind = "action"
    else:
        # Surface channel preserved; kind=message
        kind = "message"

    # Drop legacy "api" runtime tag (we now tag actual model family; unknown → drop)
    if runtime == "api":
        runtime = None
    # Drop legacy "browser" runtime tag (it was a surface label, not a model)
    if runtime == "browser":
        runtime = None

    out: dict = {
        "t": d["t"],
        "actor": actor,
        "channel": new_channel,
        "kind": kind,
        "content": text,
    }
    if runtime:
        out["runtime"] = runtime
    if meta:
        out["meta"] = meta
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path: Path = args.path
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 1

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    migrated: list[str] = []
    dropped = 0
    untouched = 0
    for ln in raw_lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            print(f"skip unparsable: {ln[:80]}", file=sys.stderr)
            dropped += 1
            continue
        new = migrate_line(d)
        if new is None:
            dropped += 1
            continue
        if new is d:
            untouched += 1
        migrated.append(json.dumps(new, ensure_ascii=False))

    print(f"input lines: {len(raw_lines)}  migrated: {len(migrated)}  dropped: {dropped}  already-new: {untouched}")

    if args.dry_run:
        print("(dry-run, no write)")
        return 0

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    path.write_text("\n".join(migrated) + "\n", encoding="utf-8")
    print(f"wrote: {path}\nbackup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
