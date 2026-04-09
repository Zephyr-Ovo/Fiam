"""Core utilities — project paths, config building, platform detection, PID."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fiam.config import FiamConfig


def _project_root() -> Path:
    """Auto-detect fiam-code root.

    This file lives at scripts/fiam_lib/core.py
    → parent = fiam_lib/ → parent = scripts/ → parent = project root
    """
    return Path(__file__).resolve().parent.parent.parent


# Ensure src/ is on sys.path and scripts/ is NOT (avoid fiam.py shadowing fiam package).
_src_dir = str(_project_root() / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
_scripts_dir = str(_project_root() / "scripts")
if _scripts_dir in sys.path:
    sys.path.remove(_scripts_dir)


def _toml_path() -> Path:
    return _project_root() / "fiam.toml"


def _build_config(args: argparse.Namespace | None = None) -> "FiamConfig":
    """Build FiamConfig: toml first, CLI args override."""
    from fiam.config import FiamConfig

    code_path = _project_root()
    toml = _toml_path()

    if toml.exists():
        config = FiamConfig.from_toml(toml, code_path)
    else:
        # Fallback: require --home from CLI
        if args is None or not getattr(args, "home", None):
            print("Error: no fiam.toml found. Run 'fiam init' first.", file=sys.stderr)
            sys.exit(1)
        config = FiamConfig(
            home_path=Path(args.home).resolve(),
            code_path=code_path,
        )

    # CLI overrides
    if args is not None:
        if getattr(args, "home", None):
            override = Path(args.home).resolve()
            config.home_path = override
            # Keep home_paths in sync
            if override not in config.home_paths:
                config.home_paths.append(override)
        if getattr(args, "debug", False):
            config.debug_mode = True
        if getattr(args, "ai_name", None):
            config.ai_name = args.ai_name
        if getattr(args, "user_name", None):
            config.user_name = args.user_name

    config.ensure_dirs()
    return config


def _detect_platform() -> str:
    """Detect OS: 'windows', 'macos', or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    elif sys.platform == "darwin":
        return "macos"
    else:
        return "linux"


def _setup_hf_cache() -> None:
    """Ensure HF_HOME points to project .cache/huggingface/ before any model loading."""
    code_path = _project_root()
    hf_home = code_path / ".cache" / "huggingface"
    os.environ["HF_HOME"] = str(hf_home)
    hf_home.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------
# PID file management
# ------------------------------------------------------------------

def _pid_path(code_path: Path) -> Path:
    return code_path / "store" / ".fiam.pid"


def _is_daemon_running(code_path: Path) -> int | None:
    """Return PID if daemon is running, None otherwise."""
    pp = _pid_path(code_path)
    if not pp.exists():
        return None
    try:
        pid = int(pp.read_text().strip())
    except (ValueError, OSError):
        pp.unlink(missing_ok=True)
        return None
    # Check if process is alive
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        pp.unlink(missing_ok=True)
        return None
