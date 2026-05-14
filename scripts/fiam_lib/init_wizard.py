"""fiam init — interactive setup wizard."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _detect_platform
from fiam_lib.hooks import write_constitution_md, write_manual_md, write_awareness_md, write_gitignore, install_hooks
from fiam_lib.ui import _conjure


def cmd_init(args: argparse.Namespace) -> None:
    """Interactive setup wizard."""
    from fiam.config import FiamConfig, LANGUAGE_PROFILES

    code_path = _project_root()
    toml = _toml_path()
    platform = _detect_platform()

    # Load existing config for defaults (if re-running init)
    existing: FiamConfig | None = None
    if toml.exists():
        try:
            existing = FiamConfig.from_toml(toml, code_path)
        except Exception:
            pass

    print()
    print("  fiam — setup")
    print(f"  platform: {platform}")
    print()

    # ── Home directory (safe: never overwrites existing files) ──
    default_home = str(existing.home_path) if existing else ""
    existing_paths: list[Path] = list(existing.home_paths) if existing else []
    if default_home:
        print(f"  Current home: {default_home}")
        if len(existing_paths) > 1:
            print(f"  All homes: {', '.join(str(p) for p in existing_paths)}")
        home_choice = input("  Keep this home? [Y/n/new path]: ").strip()
        if not home_choice or home_choice.lower() == "y":
            home_input = default_home
        else:
            home_input = home_choice
    else:
        home_input = input("  Home directory: ").strip()

    if not home_input:
        print("Error: home directory is required.", file=sys.stderr)
        sys.exit(1)
    home_path = Path(home_input).resolve()

    # Maintain home_paths list — add new path without losing existing ones
    if home_path not in existing_paths:
        existing_paths.append(home_path)
    home_paths = existing_paths if existing_paths else [home_path]

    if home_path.exists() and any(home_path.iterdir()):
        print(f"  (existing directory — files will be preserved)")
    else:
        print(f"  (will create: {home_path})")

    # ── Language profile ──
    default_lang = existing.language_profile if existing else "multi"
    print()
    print("  Language profile:")
    print("    1) zh    — 中文（中文专项模型，中文情感检测最佳）")
    print("    2) en    — English (English-specific models)")
    print("    3) multi — 双语/多语 (single multilingual model, unified vector space)")
    print()
    print("    ⚠ zh/en 各自只下载对应语言的模型，体积小，效果最优。")
    print("      选 multi 使用统一向量空间，跨语言检索准确，但模型较大。")
    print("      如果你中英文混用且希望跨语言记忆关联，选 multi。")
    print("      如果你主要用一种语言，选 zh 或 en 体验更好。")
    print()
    lang_input = input(f"  Choose [1/2/3, default={default_lang}]: ").strip()
    lang_map = {"1": "zh", "2": "en", "3": "multi"}
    language_profile = lang_map.get(lang_input, "")
    if not language_profile:
        language_profile = lang_input if lang_input in ("zh", "en", "multi") else default_lang

    profile = LANGUAGE_PROFILES[language_profile]
    print(f"  → {language_profile}: embedding={profile['embedding']}, dim={profile['embedding_dim']}")

    # ── User identity ──
    default_user = existing.user_name if existing else ""
    if default_user:
        print(f"  User: {default_user}")
        change_user = input("  Change user name? [y/N]: ").strip().lower()
        user_name = (input(f"  Your name [{default_user}]: ").strip() or default_user) if change_user == "y" else default_user
    else:
        user_name = input("  Your name [e.g. Alex]: ").strip()

    # ── Git ──
    default_git = existing.git_enabled if existing else True
    git_prompt = "Y/n" if default_git else "y/N"
    git_input = input(f"  Enable git? [{git_prompt}]: ").strip().lower()
    if git_input:
        git_enabled = git_input != "n"
    else:
        git_enabled = default_git

    print()
    _conjure()

    # Build config
    config = FiamConfig(
        home_path=home_path,
        home_paths=home_paths,
        code_path=code_path,
        user_name=user_name,
        language_profile=language_profile,
        embedding_model=str(profile["embedding"]),
        embedding_dim=int(profile["embedding_dim"]),
        git_enabled=git_enabled,
    )

    print()

    # Config file
    config.to_toml()
    print(f"  config       {config.toml_path}")

    # Home + directory structure (mkdir exist_ok — never overwrites files)
    config.ensure_dirs()
    print(f"  home         {home_path}")

    # constitution.md + .gitignore (only written if not exists)
    constitution_result = write_constitution_md(config)
    if constitution_result:
        print(f"  constitution.md  {config.constitution_md_path}")
    else:
        print(f"  constitution.md  {config.constitution_md_path}  (exists, not overwritten)")
    manual_result = write_manual_md(config)
    if manual_result:
        print(f"  manual.md        {config.manual_md_path}")
    else:
        if config.manual_md_path.exists():
            print(f"  manual.md        {config.manual_md_path}  (exists, not overwritten)")
    awareness_dest = config.self_dir / "awareness.md"
    awareness_result = write_awareness_md(config)
    if awareness_result:
        print(f"  awareness.md {awareness_dest}")
    else:
        if awareness_dest.exists():
            print(f"  awareness.md {awareness_dest}  (exists, not overwritten)")
    write_gitignore(config)

    # Auto-install hook files into home/.claude/
    hook_results = install_hooks(config, platform)
    for h in hook_results:
        print(f"  hook         {h}")

    # Git init
    if git_enabled:
        git_dir = home_path / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=str(home_path),
                           capture_output=True, check=False)
        print(f"  git          {home_path}")

    # Set HF_HOME to project cache (for all subsequent fiam commands)
    hf_home = code_path / ".cache" / "huggingface"
    os.environ["HF_HOME"] = str(hf_home)
    hf_home.mkdir(parents=True, exist_ok=True)
    print(f"  models cache {hf_home}")

    print()
    print("  ✓ Setup complete! Next:")
    print(f"    1. cd {home_path}")
    print("    2. claude")
    print("    3. (in another terminal) uv run python scripts/fiam.py start")
    print()
    print("  Next:")
    print(f"    cd {home_path} && claude   # start Claude Code from home")
    print(f"    fiam start                 # start daemon (separate terminal)")
    print()
    print("  First time? Open the dashboard annotate page after some flow has accumulated.")
    print()
