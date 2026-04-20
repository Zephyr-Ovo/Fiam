"""fiam add-home / remove-home — manage home_paths list."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _detect_platform
from fiam_lib.hooks import write_claude_md, write_gitignore, install_hooks


def cmd_add_home(args: argparse.Namespace) -> None:
    """Add a home directory to fiam.toml and set up its structure."""
    from fiam.config import FiamConfig

    code_path = _project_root()
    toml = _toml_path()

    if not toml.exists():
        print("Error: no fiam.toml found. Run 'fiam init' first.", file=sys.stderr)
        sys.exit(1)

    config = FiamConfig.from_toml(toml, code_path)
    new_path = Path(args.path).resolve()

    if new_path in config.home_paths:
        print(f"  '{new_path}' is already in home_paths.", file=sys.stderr)
        sys.exit(0)

    # Add to list and switch active home
    config.home_paths.append(new_path)
    config.home_path = new_path
    config.to_toml()
    print(f"  Added: {new_path}")

    # Create directory structure for this home
    config.ensure_dirs()

    # CLAUDE.md + .gitignore (skip if exists)
    result = write_claude_md(config)
    if result:
        print(f"  CLAUDE.md    {config.claude_md_path}")
    else:
        print(f"  CLAUDE.md    {config.claude_md_path}  (exists, not overwritten)")
    write_gitignore(config)

    # Hooks
    platform = _detect_platform()
    hook_results = install_hooks(config, platform)
    for h in hook_results:
        print(f"  hook         {h}")

    # Git init (if enabled)
    if config.git_enabled:
        import subprocess

        git_dir = new_path / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=str(new_path),
                           capture_output=True, check=False)
        print(f"  git          {new_path}")

    print()
    print(f"  ✓ Home added. Use 'fiam start --home {new_path}' to monitor it.")
    print(f"  All homes: {', '.join(str(p) for p in config.home_paths)}")


def cmd_remove_home(args: argparse.Namespace) -> None:
    """Remove a home directory from fiam.toml (data is NOT deleted)."""
    from fiam.config import FiamConfig

    code_path = _project_root()
    toml = _toml_path()

    if not toml.exists():
        print("Error: no fiam.toml found. Run 'fiam init' first.", file=sys.stderr)
        sys.exit(1)

    config = FiamConfig.from_toml(toml, code_path)
    target = Path(args.path).resolve()

    if target not in config.home_paths:
        print(f"  '{target}' is not in home_paths.", file=sys.stderr)
        print(f"  Current homes: {', '.join(str(p) for p in config.home_paths)}")
        sys.exit(1)

    if len(config.home_paths) <= 1:
        print("Error: cannot remove the last home directory.", file=sys.stderr)
        sys.exit(1)

    config.home_paths.remove(target)

    # If we removed the active home, switch to the first remaining one
    if config.home_path == target:
        config.home_path = config.home_paths[0]
        print(f"  Active home switched to: {config.home_path}")

    config.to_toml()
    print(f"  Removed: {target}")
    print(f"  (data at '{target}' was NOT deleted)")
    print(f"  Remaining homes: {', '.join(str(p) for p in config.home_paths)}")
