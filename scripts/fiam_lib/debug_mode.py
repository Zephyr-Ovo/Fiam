"""Debug profile helpers for runtime test loops."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from fiam_lib.core import _build_config, _toml_path


def cmd_debug(args: argparse.Namespace) -> None:
    action = getattr(args, "action", "status") or "status"
    toml = _toml_path()
    if not toml.exists():
        print("  No fiam.toml found. Run 'fiam init' first.", file=sys.stderr)
        sys.exit(1)

    if action in {"on", "off"}:
        set_debug_enabled(toml, action == "on")

    config = _build_config(None)
    print("  fiam debug")
    print("  ─────────────────────────────────────")
    print(f"  enabled                 {str(config.debug_mode).lower()}")
    print(f"  memory_mode             {config.memory_mode}")
    print(f"  idle_timeout_minutes    {config.idle_timeout_minutes}")
    print(f"  poll_interval_seconds   {config.poll_interval_seconds}")
    print(f"  api_tools_max_loops     {config.api_tools_max_loops}")
    print(f"  app_default_runtime     {config.app_default_runtime}")
    print(f"  app_recall_recent       {str(config.app_recall_include_recent).lower()}")

    if action in {"on", "off"}:
        print(f"  ✓ debug profile {'enabled' if action == 'on' else 'disabled'} in fiam.toml")
        if getattr(args, "restart", False):
            _restart_live_services()
        else:
            print("  restart daemon/dashboard for long-running services to pick this up")


def set_debug_enabled(toml_path: Path, enabled: bool) -> None:
    _set_toml_value(toml_path, "debug", "enabled", "true" if enabled else "false")


def _set_toml_value(toml_path: Path, section: str, key: str, value: str) -> None:
    lines = toml_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_section = False
    section_found = False
    key_written = False
    header = f"[{section}]"

    for line in lines:
        stripped = line.strip()
        is_header = stripped.startswith("[") and stripped.endswith("]")
        if is_header:
            if in_section and not key_written:
                out.append(f"{key} = {value}")
                key_written = True
            in_section = stripped == header
            section_found = section_found or in_section
            out.append(line)
            continue
        if in_section and stripped.split("=", 1)[0].strip() == key:
            out.append(f"{key} = {value}")
            key_written = True
            continue
        out.append(line)

    if section_found and in_section and not key_written:
        out.append(f"{key} = {value}")
    if not section_found:
        if out and out[-1].strip():
            out.append("")
        out.extend([header, f"{key} = {value}"])
    toml_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _restart_live_services() -> None:
    if sys.platform != "linux":
        print("  restart skipped: --restart is only supported on Linux")
        return
    subprocess.run(["sudo", "-n", "systemctl", "restart", "fiam-daemon.service"], check=False)
    start_dashboard = Path.home() / "start_dashboard.sh"
    if start_dashboard.exists():
        subprocess.run(["bash", str(start_dashboard)], check=False)
    print("  restart requested")