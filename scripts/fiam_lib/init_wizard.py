"""fiam init — interactive setup wizard."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from fiam_lib.core import _project_root, _toml_path, _detect_platform
from fiam_lib.hooks import _INJECT_PS1_TEMPLATE, _INJECT_SH_TEMPLATE
from fiam_lib.ui import _conjure


def cmd_init(args: argparse.Namespace) -> None:
    """Interactive setup wizard."""
    from fiam.config import FiamConfig, LANGUAGE_PROFILES, EMOTION_ZH_MODELS
    from fiam.injector.claude_code import write_claude_md, write_gitignore

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
    if default_home:
        print(f"  Current home: {default_home}")
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

    if home_path.exists() and any(home_path.iterdir()):
        print(f"  (existing directory — files will be preserved)")
    else:
        print(f"  (will create: {home_path})")

    # ── Language profile ──
    default_lang = existing.language_profile if existing else "multi"
    print()
    print("  Language profile:")
    print("    1) zh    — 中文（中文专项模型，中文情感检测最佳）")
    print("    2) en    — English (English-specific models, fine-grained arousal)")
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

    # ── Emotion provider ──
    default_provider = existing.emotion_provider if existing else "local"
    print()
    print("  Emotion intensity — who decides?")
    print("    1) local ★ — 本地模型 (WDI, 无需联网, 需下载 300~500 MB 情感模型)")
    print("    2) api     — LLM API (用 narrative LLM 判断, 无需下载情感模型, 消耗 token)")
    print()
    provider_input = input(f"  Choose [1/2, default={default_provider}]: ").strip()
    provider_map = {"1": "local", "2": "api"}
    emotion_provider = provider_map.get(provider_input, "")
    if not emotion_provider:
        emotion_provider = provider_input if provider_input in ("local", "api") else default_provider
    print(f"  → {emotion_provider}")

    # ── Emotion model size (local + zh/multi only) ──
    emotion_zh_model = ""
    if emotion_provider == "local":
        emotion_zh_model = str(profile.get("emotion_zh", ""))
        if emotion_zh_model:
            print()
            print("  Chinese emotion model size:")
            small = EMOTION_ZH_MODELS["small"]
            large = EMOTION_ZH_MODELS["large"]
            print(f"    1) small ★  {small['name']}")
            print(f"               backbone: {small['backbone']}, ~{small['size_mb']} MB")
            print(f"    2) large    {large['name']}")
            print(f"               backbone: {large['backbone']}, ~{large['size_mb']} MB")
            print()
            print("    两者使用相同的 8 类中文情感标签，精度差异很小。")
            print("    large 是 2.2 GB，边际提升不大，不特别执着不推荐。")
            print()
            size_input = input("  Choose [1/2, default=1 small]: ").strip()
            if size_input == "2":
                emotion_zh_model = str(large["name"])
                print(f"  → large: {emotion_zh_model}")
            else:
                emotion_zh_model = str(small["name"])
                print(f"  → small: {emotion_zh_model}")
    else:
        print("  (skip emotion model download — API handles emotion analysis)")

    # ── Identity ──
    default_ai = existing.ai_name if existing else ""
    default_user = existing.user_name if existing else ""
    ai_name = input(f"  AI name [{default_ai or 'e.g. Nova'}]: ").strip() or default_ai
    user_name = input(f"  Your name [{default_user or 'e.g. Alex'}]: ").strip() or default_user

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
        code_path=code_path,
        ai_name=ai_name,
        user_name=user_name,
        language_profile=language_profile,
        emotion_provider=emotion_provider,
        embedding_model=str(profile["embedding"]),
        embedding_dim=int(profile["embedding_dim"]),
        emotion_model_zh=emotion_zh_model if emotion_zh_model else str(profile.get("emotion_zh", "")),
        emotion_model_en=str(profile.get("emotion_en", "")),
        git_enabled=git_enabled,
    )

    print()

    # Config file
    config.to_toml()
    print(f"  config       {config.toml_path}")

    # Home + directory structure (mkdir exist_ok — never overwrites files)
    config.ensure_dirs()
    print(f"  home         {home_path}")

    # CLAUDE.md + .gitignore (only written if not exists)
    write_claude_md(config)
    print(f"  CLAUDE.md    {config.claude_md_path}")
    write_gitignore(config)

    # Auto-install hook files into home/.claude/
    claude_dir = home_path / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Platform-aware hook installation
    if platform == "windows":
        inject_path = hooks_dir / "inject.ps1"
        hook_template = _INJECT_PS1_TEMPLATE
        hook_command = r'& "$env:CLAUDE_PROJECT_DIR\.claude\hooks\inject.ps1"'
        hook_shell = "powershell"
    else:
        inject_path = hooks_dir / "inject.sh"
        hook_template = _INJECT_SH_TEMPLATE
        hook_command = '"$CLAUDE_PROJECT_DIR/.claude/hooks/inject.sh"'
        hook_shell = "bash"

    if not inject_path.exists():
        inject_path.write_text(hook_template, encoding="utf-8")
        if platform != "windows":
            inject_path.chmod(0o755)
        print(f"  hook         {inject_path}")
    else:
        print(f"  hook         {inject_path}  (exists, not overwritten)")

    settings_path = claude_dir / "settings.local.json"
    if not settings_path.exists():
        hook_settings = {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "shell": hook_shell,
                                "command": hook_command,
                                "statusMessage": "checking memory...",
                            }
                        ]
                    }
                ]
            }
        }
        settings_path.write_text(json.dumps(hook_settings, indent=2), encoding="utf-8")
        print(f"  hook config  {settings_path}")
    else:
        print(f"  hook config  {settings_path}  (exists, not overwritten)")

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
    print("  First time? Import existing CC history:")
    print(f"    fiam scan                  # one-time full history scan")
    print()
