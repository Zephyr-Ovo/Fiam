"""
fiam — unified entry point.

Primary workflow:
  fiam init                      — interactive setup wizard
  fiam start                     — daemon: watch → slice → store → refresh
  fiam stop                      — stop a running daemon
  fiam status                    — check daemon + memory stats

Debug / manual:
  fiam pre     --home <home>     — run pre_session once
  fiam post    --home <home>     — run post_session once
  fiam reindex --home <home>     — rebuild all embeddings with current models
  fiam find-sessions --home <home>
"""

from __future__ import annotations

import argparse

# This import triggers sys.path setup (adds src/, removes scripts/).
from fiam_lib.core import _setup_hf_cache


def main() -> None:
    _setup_hf_cache()

    parser = argparse.ArgumentParser(
        prog="fiam",
        description="fiam — Fluid Injected Affective Memory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common arguments for commands that need --home
    def add_common(sub: argparse.ArgumentParser, home_required: bool = False) -> None:
        sub.add_argument(
            "--home", required=home_required, type=str, default=None,
            help="Path to the AI's home directory (overrides fiam.toml)",
        )
        sub.add_argument(
            "--debug", action="store_true", default=False,
            help="Enable debug mode (verbose output)",
        )

    # ── Commands ──

    # init — no --home needed (interactive)
    sub_init = subparsers.add_parser("init", help="Interactive setup wizard")
    sub_init.set_defaults(func=_cmd_init)

    # start
    sub_start = subparsers.add_parser("start", help="Start daemon (watch → slice → store)")
    add_common(sub_start)
    sub_start.set_defaults(func=_cmd_start)

    # stop
    sub_stop = subparsers.add_parser("stop", help="Stop running daemon")
    sub_stop.set_defaults(func=_cmd_stop)

    # status
    sub_status = subparsers.add_parser("status", help="Show daemon status and memory stats")
    sub_status.set_defaults(func=_cmd_status)

    # reindex
    sub_reindex = subparsers.add_parser("reindex", help="Rebuild all embeddings")
    add_common(sub_reindex)
    sub_reindex.set_defaults(func=_cmd_reindex)

    # pre (debug)
    sub_pre = subparsers.add_parser("pre", help="Run pre_session once (debug)")
    add_common(sub_pre)
    sub_pre.set_defaults(func=_cmd_pre)

    # post (debug)
    sub_post = subparsers.add_parser("post", help="Run post_session once (debug)")
    add_common(sub_post)
    sub_post.add_argument("--test-file", type=str, default=None,
                          help="Path to a test fixture JSON")
    sub_post.add_argument("--force", action="store_true", default=False,
                          help="Reprocess from start of JSONL")
    sub_post.set_defaults(func=_cmd_post)

    # session (legacy)
    sub_session = subparsers.add_parser("session", help="Legacy: pre → claude → post")
    add_common(sub_session)
    sub_session.set_defaults(func=_cmd_session)

    # find-sessions (debug)
    sub_find = subparsers.add_parser("find-sessions", help="List JSONL files (debug)")
    add_common(sub_find)
    sub_find.set_defaults(func=_cmd_find_sessions)

    # clean (reset store)
    sub_clean = subparsers.add_parser("clean", help="Reset store to factory-fresh state")
    sub_clean.add_argument("-y", "--yes", action="store_true", default=False,
                           help="Skip confirmation prompt")
    sub_clean.set_defaults(func=_cmd_clean)

    # scan (one-time history import)
    sub_scan = subparsers.add_parser("scan", help="One-time scan of all JSONL history")
    add_common(sub_scan)
    sub_scan.add_argument("--force", action="store_true", default=False,
                          help="Re-scan all files from scratch (ignores cursor)")
    sub_scan.set_defaults(func=_cmd_scan)

    # settings
    sub_settings = subparsers.add_parser("settings", help="View/edit fiam.toml configuration")
    sub_settings.add_argument("--set", nargs="*", metavar="KEY=VALUE",
                              help="Set one or more values directly (e.g. --set top_k=8 llm_enabled=true)")
    sub_settings.set_defaults(func=_cmd_settings)

    # feedback (interactive event rating)
    sub_feedback = subparsers.add_parser("feedback", help="Rate recent events (TUI: ←👎 →👍)")
    add_common(sub_feedback)
    sub_feedback.add_argument("-n", "--count", type=int, default=8,
                              help="Number of recent events to show (default: 8)")
    sub_feedback.set_defaults(func=_cmd_feedback)

    # graph (1.0 easter egg)
    sub_graph = subparsers.add_parser("graph", help="Generate Obsidian wikilink graph")
    add_common(sub_graph)
    sub_graph.add_argument("--threshold", type=float, default=0.75,
                           help="Cosine similarity threshold for wikilinks (default: 0.75)")
    sub_graph.set_defaults(func=_cmd_graph)

    # rem (memory consolidation)
    sub_rem = subparsers.add_parser("rem", help="Consolidate similar events via LLM (TUI)")
    add_common(sub_rem)
    sub_rem.add_argument("--threshold", type=float, default=0.82,
                         help="Cosine similarity threshold for clustering (default: 0.82)")
    sub_rem.set_defaults(func=_cmd_rem)

    # import (Claude Web export)
    sub_import = subparsers.add_parser("import", help="Import Claude Web export (conversations.json)")
    add_common(sub_import)
    sub_import.add_argument("file", type=str,
                            help="Path to conversations.json from Claude Web export")
    sub_import.set_defaults(func=_cmd_import)

    args = parser.parse_args()
    args.func(args)


# ------------------------------------------------------------------
# Lazy command wrappers — import on demand to keep startup fast
# ------------------------------------------------------------------

def _cmd_init(args):
    from fiam_lib.init_wizard import cmd_init
    cmd_init(args)

def _cmd_start(args):
    from fiam_lib.daemon import cmd_start
    cmd_start(args)

def _cmd_stop(args):
    from fiam_lib.daemon import cmd_stop
    cmd_stop(args)

def _cmd_status(args):
    from fiam_lib.daemon import cmd_status
    cmd_status(args)

def _cmd_reindex(args):
    from fiam_lib.storage import cmd_reindex
    cmd_reindex(args)

def _cmd_pre(args):
    from fiam_lib.session import cmd_pre
    cmd_pre(args)

def _cmd_post(args):
    from fiam_lib.session import cmd_post
    cmd_post(args)

def _cmd_session(args):
    from fiam_lib.session import cmd_session
    cmd_session(args)

def _cmd_find_sessions(args):
    from fiam_lib.storage import cmd_find_sessions
    cmd_find_sessions(args)

def _cmd_clean(args):
    from fiam_lib.storage import cmd_clean
    cmd_clean(args)

def _cmd_scan(args):
    from fiam_lib.storage import cmd_scan
    cmd_scan(args)

def _cmd_settings(args):
    from fiam_lib.settings import cmd_settings
    cmd_settings(args)

def _cmd_feedback(args):
    from fiam_lib.feedback import cmd_feedback
    cmd_feedback(args)

def _cmd_graph(args):
    from fiam_lib.graph import cmd_graph
    cmd_graph(args)

def _cmd_rem(args):
    from fiam_lib.rem import cmd_rem
    cmd_rem(args)

def _cmd_import(args):
    from fiam_lib.web_import import cmd_import
    cmd_import(args)


if __name__ == "__main__":
    main()
