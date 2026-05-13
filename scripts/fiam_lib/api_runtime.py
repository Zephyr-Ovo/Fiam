"""Manual API runtime command."""

from __future__ import annotations

import argparse

from fiam_lib.core import _build_config, _load_env_file, _project_root


def cmd_api(args: argparse.Namespace) -> None:
    """Call the configured API runtime once."""
    config = _build_config(args)
    _load_env_file(_project_root())

    from fiam.runtime.api import ApiRuntime

    runtime = ApiRuntime.from_config(config)

    result = runtime.ask(
        args.text,
        channel=getattr(args, "channel", "api") or "api",
    )
    print(result.reply)
    if getattr(args, "debug", False):
        print(f"\n[api] model={result.model} usage={result.usage} recall={result.recall_fragments}")
