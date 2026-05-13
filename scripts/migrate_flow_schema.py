"""One-shot migration of flow.jsonl from the old beat schema to the new one.

Old: {t, actor, channel, text, runtime?, user_status?, ai_status?, meta?}
    channel could be: favilla / app / browser / cc / system / think / action
     runtime could be: cc / api / browser / claude / gemini / None

New: {t, actor, channel, surface?, kind, content, runtime?, meta?}
    channel ∈ canonical channel (chat, browser, stroll, email, studio, cc, system, ...)
     kind    ∈ {message, action, tool_result, think, schedule}
     runtime ∈ {cc, claude, gemini, ...} (no "api", no "browser")

Usage: python scripts/migrate_flow_schema.py path/to/flow.jsonl [--dry-run]
A backup .bak is written next to the input.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
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


def normalize_surface(channel: str, surface) -> str:
    clean = str(surface or "").strip().lower()
    if channel in {"favilla", "app"}:
        return "favilla" if not clean or clean in {"favilla", "app"} or clean.startswith("favilla.") else clean
    if clean == "app" or clean.startswith("favilla."):
        return "favilla"
    if clean.startswith("atrium."):
        return "atrium"
    return clean


def migrate_line(d: dict) -> dict | None:
    """Return the migrated beat dict, or None to drop the line."""
    if "content" in d and "kind" in d:
        out = dict(d)
        channel = str(out.get("channel") or "").strip().lower()
        surface = normalize_surface(channel, out.get("surface"))
        if channel in {"favilla", "app"}:
            out["channel"] = "chat"
            out["surface"] = surface or "favilla"
        elif surface:
            out["surface"] = surface
        return out

    actor = d.get("actor", "system")
    old_channel = d.get("channel", "")
    text = d.get("text", d.get("content", ""))
    old_runtime = d.get("runtime")
    meta = dict(d.get("meta") or {})

    # Default mapping
    new_channel = old_channel
    surface = d.get("surface")
    kind = "message"
    runtime = old_runtime

    if old_channel in {"favilla", "app"}:
        new_channel = "chat"
        surface = normalize_surface(old_channel, surface)

    if old_channel == "think":
        # Belongs on the surface that produced it. Old code stored think under "think".
        # Best guess: cc thoughts came from cc runtime; otherwise treat as favilla.
        new_channel = "cc" if old_runtime == "cc" else "chat"
        if new_channel == "chat" and not surface:
            surface = "favilla"
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
    surface = normalize_surface(new_channel, surface)

    out: dict = {
        "t": d["t"],
        "actor": actor,
        "channel": new_channel,
        "kind": kind,
        "content": text,
    }
    if runtime:
        out["runtime"] = runtime
    if surface:
        out["surface"] = surface
    if meta:
        out["meta"] = meta
    return out


def _normalize_sqlite_meta(meta_json: str) -> tuple[str, bool]:
    try:
        meta = json.loads(meta_json or "{}")
    except json.JSONDecodeError:
        return meta_json, False
    if not isinstance(meta, dict):
        return meta_json, False
    surface = normalize_surface("", meta.get("surface"))
    if surface and surface != meta.get("surface"):
        meta = dict(meta)
        meta["surface"] = surface
        return json.dumps(meta, ensure_ascii=False), True
    return meta_json, False


def migrate_sqlite(db_path: Path, *, dry_run: bool = False) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    changed = 0
    try:
        rows = conn.execute("SELECT id, channel, surface, meta_json FROM events").fetchall()
        updates: list[tuple[str, str, str, str]] = []
        for row in rows:
            channel = str(row["channel"] or "").strip().lower()
            surface = str(row["surface"] or "").strip().lower()
            new_channel = "chat" if channel in {"favilla", "app"} else channel
            new_surface = normalize_surface(channel, surface)
            if channel in {"favilla", "app"} and not new_surface:
                new_surface = "favilla"
            new_meta_json, meta_changed = _normalize_sqlite_meta(str(row["meta_json"] or "{}"))
            if new_channel != channel or new_surface != surface or meta_changed:
                updates.append((new_channel, new_surface, new_meta_json, row["id"]))
        changed = len(updates)
        if updates and not dry_run:
            backup = db_path.with_name(db_path.name + ".bak")
            shutil.copy2(db_path, backup)
            conn.executemany(
                "UPDATE events SET channel = ?, surface = ?, meta_json = ? WHERE id = ?",
                updates,
            )
            conn.commit()
    finally:
        conn.close()
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path: Path = args.path
    db_path = path.parent / "events.sqlite3"
    if not path.exists() and not db_path.exists():
        print(f"no such file or sqlite db: {path}", file=sys.stderr)
        return 1

    migrated: list[str] = []
    changed = False
    if path.exists():
        raw_lines = path.read_text(encoding="utf-8").splitlines()
        for line in raw_lines:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception as e:
                print(f"skip invalid line: {e}", file=sys.stderr)
                continue
            m = migrate_line(d)
            if m is None:
                changed = True
                continue
            if m != d:
                changed = True
            migrated.append(json.dumps(m, ensure_ascii=False))
        print(f"jsonl lines: {len(raw_lines)} → {len(migrated)}, changed={changed}")

    sqlite_changed = migrate_sqlite(db_path, dry_run=args.dry_run)
    if db_path.exists():
        print(f"sqlite rows changed: {sqlite_changed}")

    if args.dry_run:
        print("(dry-run, no write)")
        return 0

    if path.exists() and changed:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        path.write_text("\n".join(migrated) + "\n", encoding="utf-8")
        print(f"wrote: {path}\nbackup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
