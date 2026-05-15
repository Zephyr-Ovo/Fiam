from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collectors.edit import collect_edit_events
from .collectors.system import collect_system_events
from .collectors.work import collect_work_events
from .config import load_track_config
from .recall import recall as recall_text
from .summarizer import build_summarizer, summarize_edits, summarize_system
from .writer import write_track


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"--since: invalid ISO datetime: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = load_track_config(_default_toml_path())
    vault = Path(args.vault) if args.vault else cfg.vault_dir
    since = _parse_since(args.since)
    fn = build_summarizer(cfg)

    if args.name == "edit":
        events = collect_edit_events(vault, since=since, limit=args.limit)
        if not events:
            print("no commits found; nothing written", file=sys.stderr)
            return 0
        body = summarize_edits(events, summarize_fn=fn)
    elif args.name == "work":
        code_dir = Path(args.code_dir) if args.code_dir else cfg.code_dir
        events = collect_work_events(code_dir, since=since, limit=args.limit)
        if not events:
            print("no commits found; nothing written", file=sys.stderr)
            return 0
        body = summarize_edits(events, summarize_fn=fn)
    elif args.name == "system":
        store_dir = Path(args.store_dir) if args.store_dir else cfg.store_dir
        sys_events = collect_system_events(store_dir, since=since, limit=args.limit)
        if not sys_events:
            print("no trace events found; nothing written", file=sys.stderr)
            return 0
        body = summarize_system(sys_events, summarize_fn=fn)
    else:
        print(f"track collector {args.name!r} not implemented (available: edit, work, system)", file=sys.stderr)
        return 2

    target = write_track(vault, args.name, body)
    count = len(sys_events) if args.name == "system" else len(events)
    print(f"wrote {target} ({count} events)")
    return 0


def _cmd_recall(args: argparse.Namespace) -> int:
    cfg = load_track_config(_default_toml_path())
    vault = Path(args.vault) if args.vault else cfg.vault_dir
    since = _parse_since(args.since)
    out = recall_text(vault, args.name, since=since)
    sys.stdout.write(out)
    return 0


def _default_toml_path() -> Path:
    # repo root is two parents above this file (src/fiam/track/__main__.py)
    return Path(__file__).resolve().parents[3] / "fiam.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fiam.track")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="collect → summarize → write a track")
    run.add_argument("name", help="track collector name (edit, work, system)")
    run.add_argument("--vault", help="override vault dir")
    run.add_argument("--since", help="ISO datetime; only events after this")
    run.add_argument("--limit", type=int, default=None, help="max events")
    run.add_argument("--code-dir", dest="code_dir", help="override code repo dir (work collector)")
    run.add_argument("--store-dir", dest="store_dir", help="override store dir (system collector)")
    run.set_defaults(func=_cmd_run)

    rec = sub.add_parser("recall", help="render a track with time-decay folding")
    rec.add_argument("name", help="track name (file under track/<name>.md)")
    rec.add_argument("--vault", help="override vault dir")
    rec.add_argument("--since", help="ISO datetime; hide older sections")
    rec.set_defaults(func=_cmd_recall)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
