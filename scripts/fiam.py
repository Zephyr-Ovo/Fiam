"""
fiam — unified entry point.

Primary workflow:
  fiam init                      — interactive setup wizard
  fiam start                     — daemon: watch → slice → store → refresh
  fiam stop                      — stop a running daemon
  fiam status                    — check daemon + memory stats

Debug / manual:
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

    # debug profile
    sub_debug = subparsers.add_parser("debug", help="View or toggle the runtime debug profile")
    sub_debug.add_argument("action", nargs="?", choices=("status", "on", "off"), default="status",
                           help="Debug profile action")
    sub_debug.add_argument("--restart", action="store_true", default=False,
                           help="Restart live services after toggling on Linux")
    sub_debug.set_defaults(func=_cmd_debug)

    # find-sessions (debug)
    sub_find = subparsers.add_parser("find-sessions", help="List JSONL files (debug)")
    add_common(sub_find)
    sub_find.set_defaults(func=_cmd_find_sessions)

    # clean / clear (reset generated state)
    sub_clean = subparsers.add_parser("clean", aliases=["clear"], help="Reset generated state to a whiteboard test state")
    add_common(sub_clean)
    sub_clean.add_argument("-y", "--yes", action="store_true", default=False,
                           help="Skip confirmation prompt")
    sub_clean.set_defaults(func=_cmd_clean)

    # settings
    sub_settings = subparsers.add_parser("settings", help="View/edit fiam.toml configuration")
    sub_settings.add_argument("--set", nargs="*", metavar="KEY=VALUE",
                              help="Set one or more values directly (e.g. --set top_k=8 llm_enabled=true)")
    sub_settings.set_defaults(func=_cmd_settings)

    # add-home
    sub_add_home = subparsers.add_parser("add-home", help="Add a home directory")
    sub_add_home.add_argument("path", type=str, help="Path to the new home directory")
    sub_add_home.set_defaults(func=_cmd_add_home)

    # remove-home
    sub_rm_home = subparsers.add_parser("remove-home", help="Remove a home directory (data NOT deleted)")
    sub_rm_home.add_argument("path", type=str, help="Path of the home directory to remove")
    sub_rm_home.set_defaults(func=_cmd_remove_home)

    # plugin registry
    sub_plugin = subparsers.add_parser("plugin", help="List or toggle optional integration plugins")
    add_common(sub_plugin)
    plugin_sub = sub_plugin.add_subparsers(dest="plugin_action", required=True)
    plugin_sub.add_parser("list", help="List plugin manifests")
    plugin_show = plugin_sub.add_parser("show", help="Show one plugin manifest")
    plugin_show.add_argument("plugin_id", type=str)
    plugin_enable = plugin_sub.add_parser("enable", help="Enable a plugin manifest")
    plugin_enable.add_argument("plugin_id", type=str)
    plugin_disable = plugin_sub.add_parser("disable", help="Disable a plugin manifest")
    plugin_disable.add_argument("plugin_id", type=str)
    sub_plugin.set_defaults(func=_cmd_plugin)

    # api — manual API runtime smoke call
    sub_api = subparsers.add_parser("api", help="Call configured API runtime once")
    add_common(sub_api)
    sub_api.add_argument("text", type=str, help="User text to send to the API runtime")
    sub_api.add_argument("--source", type=str, default="cli", help="Source label for prompt/flow metadata")
    sub_api.add_argument("--no-record", action="store_true", default=False,
                         help="Do not write the call into flow.jsonl")
    sub_api.set_defaults(func=_cmd_api)

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

def _cmd_debug(args):
    from fiam_lib.debug_mode import cmd_debug
    cmd_debug(args)

def _cmd_find_sessions(args):
    from fiam_lib.maintenance import cmd_find_sessions
    cmd_find_sessions(args)

def _cmd_clean(args):
    from fiam_lib.maintenance import cmd_clean
    cmd_clean(args)

def _cmd_settings(args):
    from fiam_lib.settings import cmd_settings
    cmd_settings(args)

def _cmd_add_home(args):
    from fiam_lib.home_mgmt import cmd_add_home
    cmd_add_home(args)

def _cmd_remove_home(args):
    from fiam_lib.home_mgmt import cmd_remove_home
    cmd_remove_home(args)

def _cmd_plugin(args):
    from fiam_lib.plugins import cmd_plugin
    cmd_plugin(args)

def _cmd_api(args):
    from fiam_lib.api_runtime import cmd_api
    cmd_api(args)

if __name__ == "__main__":
    main()
