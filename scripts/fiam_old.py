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
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fiam.config import FiamConfig


def _project_root() -> Path:
    """Auto-detect fiam-code root (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


# Ensure src/ is on sys.path before any fiam imports.
_src_dir = str(_project_root() / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
_scripts_dir = str(Path(__file__).resolve().parent)
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
            config.home_path = Path(args.home).resolve()
        if getattr(args, "debug", False):
            config.debug_mode = True
        if getattr(args, "ai_name", None):
            config.ai_name = args.ai_name
        if getattr(args, "user_name", None):
            config.user_name = args.user_name

    config.ensure_dirs()
    return config


# ------------------------------------------------------------------
# JSONL session file discovery
# ------------------------------------------------------------------

def _claude_projects_dir() -> Path:
    """Locate Claude Code's projects directory.

    All platforms: ~/.claude/projects/
    """
    return Path.home() / ".claude" / "projects"


def _sanitize_home_path(home_path: Path) -> str:
    """Derive the sanitized directory name Claude Code uses.

    Claude Code replaces path separators and colons with dashes.
    e.g. D:\\ai-home → D:-ai-home → D--ai-home
    """
    raw = str(home_path.resolve())
    # Replace backslashes, then colons with dashes
    sanitized = raw.replace("\\", "-").replace(":", "-")
    return sanitized


def _find_latest_jsonl(home_path: Path, *, debug: bool = False) -> Path | None:
    """Find the most recently modified JSONL file for the home project."""
    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(home_path)
    expected_dir = projects_dir / sanitized

    if debug:
        print(f"[find_jsonl] projects dir: {projects_dir}")
        print(f"[find_jsonl] sanitized home name: {sanitized}")
        print(f"[find_jsonl] expected dir: {expected_dir}")
        print(f"[find_jsonl] exists: {expected_dir.is_dir()}")

    # Try direct sanitized-path match first
    if expected_dir.is_dir():
        result = _latest_in_dir(expected_dir)
        if debug and result:
            print(f"[find_jsonl] matched exact dir → {result}")
        return result

    # Fallback: scan all project directories for the most recent JSONL
    if debug:
        print(f"[find_jsonl] exact dir not found, scanning all subdirs...")

    if projects_dir.is_dir():
        latest: Path | None = None
        latest_mtime = 0.0
        for subdir in projects_dir.iterdir():
            if subdir.is_dir():
                candidate = _latest_in_dir(subdir)
                if candidate and candidate.stat().st_mtime > latest_mtime:
                    latest = candidate
                    latest_mtime = candidate.stat().st_mtime
        if debug and latest:
            print(f"[find_jsonl] fallback found → {latest}")
        return latest

    return None


def _latest_in_dir(directory: Path) -> Path | None:
    """Return the most recently modified .jsonl file in a directory."""
    jsonl_files = list(directory.glob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


# ------------------------------------------------------------------
# Processed-session cursor (byte-offset tracking, replaces old registry)
# ------------------------------------------------------------------

def _cursor_path(code_path: Path) -> Path:
    return code_path / "store" / "cursor.json"


def _load_cursor(code_path: Path) -> dict[str, dict]:
    """Load {jsonl_abs_path: {"byte_offset": int, "mtime": float}}."""
    cp = _cursor_path(code_path)
    if not cp.exists():
        return {}
    try:
        return json.loads(cp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cursor(code_path: Path, cursor: dict[str, dict]) -> None:
    cp = _cursor_path(code_path)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(cursor, indent=2), encoding="utf-8")


# ------------------------------------------------------------------
# JSONL parsing — delegated to adapter
# ------------------------------------------------------------------

def _get_adapter():
    """Return the conversation adapter (currently Claude Code only)."""
    from fiam.adapter import get_adapter
    return get_adapter("claude_code")


def _parse_jsonl(jsonl_path: Path) -> list[dict[str, str]]:
    """Parse a Claude Code JSONL session file into conversation turns."""
    return _get_adapter().parse(jsonl_path)


def _parse_jsonl_from(jsonl_path: Path, byte_offset: int = 0) -> tuple[list[dict[str, str]], int]:
    """Parse JSONL starting from *byte_offset*. Returns (turns, new_offset)."""
    return _get_adapter().parse_incremental(jsonl_path, byte_offset)


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


# ------------------------------------------------------------------
# Rich colour palette  (purple → blue → pink → yellow → mint → back)
# ------------------------------------------------------------------

from rich.console import Console as _Console
from rich.text import Text as _Text
from rich.live import Live as _Live

_console = _Console(highlight=False)

# Build a smooth 40-stop gradient by linearly interpolating between key hues.
# Each stop is (R, G, B) in 0-255.  We cycle: purple→blue→pink→yellow→mint→purple
def _lerp_hex(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"

_KEY_HUES = ["#b57bee", "#7eb8f7", "#f7a8d0", "#f7e08a", "#a8f0e8", "#b57bee"]
_STEPS_PER_SEGMENT = 8

_PAL: list[str] = []
for _i in range(len(_KEY_HUES) - 1):
    for _s in range(_STEPS_PER_SEGMENT):
        _PAL.append(_lerp_hex(_KEY_HUES[_i], _KEY_HUES[_i + 1], _s / _STEPS_PER_SEGMENT))
_PAL_LEN = len(_PAL)  # 40


def _flow(text: str, offset: int = 0, bold: bool = True) -> _Text:
    """Colour each non-space character at a different palette position.

    Incrementing `offset` by 1 per frame shifts the whole gradient left by one
    stop — at 5 fps that produces a smooth sweeping rainbow.
    """
    t = _Text()
    ci = 0
    for ch in text:
        if ch == " ":
            t.append(" ")
        else:
            col = _PAL[(ci + offset) % _PAL_LEN]
            style = f"bold {col}" if bold else col
            t.append(ch, style=style)
            ci += 1
    return t


# ------------------------------------------------------------------
# Conjuration animation
# ------------------------------------------------------------------

def _conjure() -> None:
    """Play the Latin-progression loading animation during setup.

    fio (I become) → fiam (I will become) → fiet (it will happen)
    → fiat (let it be done) → fiat lux ✦

    Each word sweeps the full palette so colours keep flowing as text grows.
    """
    words = ["fio", "fiam", "fiet", "fiat", "fiat lux ✦"]
    # smoothly rotate through palette within each word display (~12 frames/word)
    frames_per_word = 12
    step = 0.22 / frames_per_word
    frame = 0
    with _Live("", refresh_per_second=30, console=_console, transient=False) as live:
        for word in words:
            for _ in range(frames_per_word):
                live.update(_flow(f"  {word:<22}", frame))
                frame += 1
                time.sleep(step)
    _console.print()


# ------------------------------------------------------------------
# Daemon animation
# ------------------------------------------------------------------

_ANIM_IDLE = [
    "( ˘ω˘ )  zzZ  ",
    "( ˘ω˘ ) zzZ   ",
    "( ˘ω˘ )  Zzz  ",
    "( ˘ω˘ )   zz  ",
    "( ˘ω˘ )    z  ",
    "( ˘ω˘ )       ",
    "( ˘ω˘ )       ",
    "( ˘ω˘ )  zzZ  ",
]
_ANIM_ACTIVE = [
    "( °ω° )   ·   ",
    "( °ω° )  ··   ",
    "( °ω° )  ···  ",
    "( °ω° )  ··   ",
]


def _animated_sleep(seconds: float, frames: list[str], stop_check=None) -> None:
    """Animate one line with flowing colour for `seconds`.

    Runs at 8 fps (0.125 s/frame).  offset increments every frame to sweep
    the palette continuously — the full 40-stop cycle takes ~5 s.
    """
    step = 0.125
    n = max(1, int(seconds / step))
    with _Live("", refresh_per_second=10, console=_console, transient=True) as live:
        for i in range(n):
            if stop_check and stop_check():
                break
            ts = time.strftime("%H:%M")
            line = _flow(f"  {frames[i % len(frames)]}  {ts}", i)
            live.update(line)
            time.sleep(step)


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------

def _write_recall(config: "FiamConfig", events: list, ai_name: str | None = None) -> Path:
    """Write recall.md — memory fragments surfaced by semantic retrieval.

    Each event body is stored as [user]\\n...\\n[assistant]\\n...
    We distill it into a clean one-line summary: user's words + topic hint.
    The result reads like background knowledge, not a conversation log.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [f"<!-- recall | {timestamp} -->", ""]

    for ev in events:
        age = now - ev.time
        if age.days > 30:
            time_hint = f"{age.days // 30}个月前"
        elif age.days > 0:
            time_hint = f"{age.days}天前"
        elif age.seconds > 3600:
            time_hint = f"{age.seconds // 3600}小时前"
        else:
            time_hint = "刚才"

        # Extract user-side text from event body (strip role markers)
        fragment = _extract_memory_fragment(ev.body)
        if len(fragment) > 200:
            fragment = fragment[:197] + "..."

        lines.append(f"- ({time_hint}) {fragment}")

    content = "\n".join(lines) + "\n"
    path = config.background_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _extract_memory_fragment(body: str) -> str:
    """Distill an event body into a clean memory fragment.

    Event bodies are stored as:
        [user]
        啊数据全丢了...
        [assistant]
        别急...

    We extract the user's words as the primary memory content.
    If user text is very short, include assistant's response for context.
    """
    parts = re.split(r'\[(?:user|assistant)\]\s*', body)
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return body.strip()[:200]

    # parts[0] = user text, parts[1] = assistant text (if exists)
    user_text = parts[0]

    # If user text is very short and we have assistant context, add it
    if len(user_text) < 30 and len(parts) > 1:
        asst_text = parts[1]
        if len(asst_text) < 100:
            return f"{user_text} → {asst_text}"

    return user_text


# ------------------------------------------------------------------
# Hook templates (auto-installed into home/.claude/ by fiam init)
# ------------------------------------------------------------------

_INJECT_PS1_TEMPLATE = """\
# fiam hook: inject recall.md as additionalContext on every user prompt
$recallFile = Join-Path $env:CLAUDE_PROJECT_DIR "recall.md"
if (Test-Path $recallFile) {
    $raw = Get-Content $recallFile -Raw -ErrorAction SilentlyContinue
    if ($raw -and $raw.Trim().Length -gt 0) {
        $clean = ($raw -replace '<!--.*?-->', '').Trim()
        if ($clean.Length -eq 0) { exit 0 }
        $e = $clean.Replace('\\', '\\\\').Replace('"', '\\"').Replace("`r`n", '\\n').Replace("`n", '\\n')
        Write-Output "{`"hookSpecificOutput`":{`"hookEventName`":`"UserPromptSubmit`",`"additionalContext`":`"$e`"}}"
        exit 0
    }
}
exit 0
"""

_INJECT_SH_TEMPLATE = """\
#!/bin/bash
# fiam hook: inject recall.md as additionalContext on every user prompt
RECALL_FILE="$CLAUDE_PROJECT_DIR/recall.md"
if [ -f "$RECALL_FILE" ] && [ -s "$RECALL_FILE" ]; then
    CONTENT=$(sed 's/<!--.*-->//g' "$RECALL_FILE" | tr -s '\\n' | sed '/^$/d')
    if [ -n "$CONTENT" ]; then
        ESCAPED=$(echo "$CONTENT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read())[1:-1])")
        echo "{\\"hookSpecificOutput\\":{\\"hookEventName\\":\\"UserPromptSubmit\\",\\"additionalContext\\":\\"$ESCAPED\\"}}"
        exit 0
    fi
fi
exit 0
"""


def _detect_platform() -> str:
    """Detect OS: 'windows', 'macos', or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    elif sys.platform == "darwin":
        return "macos"
    else:
        return "linux"


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


def cmd_start(args: argparse.Namespace) -> None:
    """Daemon: poll JSONL for activity, process on idle timeout."""
    config = _build_config(args)
    code_path = _project_root()

    # PID check
    existing_pid = _is_daemon_running(code_path)
    if existing_pid:
        print(f"fiam is already running (PID {existing_pid}).", file=sys.stderr)
        print("  Use 'fiam stop' first.", file=sys.stderr)
        sys.exit(1)

    # Write PID
    pid_file = _pid_path(code_path)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    # Graceful shutdown
    running = True

    def _shutdown(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Initial pre_session
    from fiam.pipeline import pre_session, post_session
    from fiam.retriever.embedder import Embedder
    from fiam.store.home import HomeStore
    import numpy as np

    result = pre_session(config)
    event_count = result["event_count"]

    _console.print()
    _console.print(_flow("  fiam  \u2726"))
    _console.print("  [dim #b57bee]\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500[/]")
    _console.print(f"  [#7eb8f7]home[/]    {config.home_path}")
    _console.print(f"  [#f7a8d0]memory[/]  {event_count} events")
    _console.print()

    # Find the JSONL directory for this home
    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(config.home_path)
    jsonl_dir = projects_dir / sanitized

    last_activity: float = 0.0
    active = False
    idle_timeout = config.idle_timeout_minutes * 60
    poll_interval = config.poll_interval_seconds

    # Live recall state
    recall_query_vec: np.ndarray | None = None  # embedding of last recall query
    recall_drift_threshold = 0.65               # cosine sim below this = topic shift
    recall_min_chars = 40                       # skip trivial messages
    embedder_lazy: Embedder | None = None

    def _get_embedder() -> Embedder:
        nonlocal embedder_lazy
        if embedder_lazy is None:
            embedder_lazy = Embedder(config)
        return embedder_lazy

    def _peek_recent_user_text(jsonl_files: list[Path], max_chars: int = 600) -> str:
        """Read the last ~max_chars of user text from JSONL files (read-only peek)."""
        parts: list[str] = []
        total = 0
        for jf in sorted(jsonl_files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                raw = jf.read_bytes()
            except OSError:
                continue
            # Walk lines backward
            for raw_line in reversed(raw.split(b"\n")):
                line_text = raw_line.decode("utf-8", errors="replace").strip()
                if not line_text:
                    continue
                try:
                    obj = json.loads(line_text)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "user":
                    msg = obj.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        parts.append(content.strip())
                        total += len(content)
                        if total >= max_chars:
                            break
            if total >= max_chars:
                break
        # Return in chronological order
        parts.reverse()
        return "\n".join(parts)

    def _update_recall_if_drifted(jsonl_files: list[Path]) -> None:
        """Check if conversation topic drifted from current recall; update if so."""
        nonlocal recall_query_vec

        user_text = _peek_recent_user_text(jsonl_files)
        if len(user_text) < recall_min_chars:
            return

        emb = _get_embedder()
        current_vec = emb.embed(user_text)

        # Check drift against last recall query
        if recall_query_vec is not None:
            sim = float(np.dot(current_vec, recall_query_vec) / (
                np.linalg.norm(current_vec) * np.linalg.norm(recall_query_vec)
            ))
            if sim > recall_drift_threshold:
                return  # topic hasn't shifted enough

        # Topic shifted — run retrieval and update recall.md
        recall_query_vec = current_vec

        store = HomeStore(config)
        from fiam.retriever import joint as joint_retriever
        events = joint_retriever.search(user_text, store, config)

        if not events:
            return

        _write_recall(config, events)
        ts = time.strftime("%H:%M")
        _console.print(f"  [dim]└[{ts}][/dim] [bold #7eb8f7]↻[/]  recall  [bold #f7e08a]{len(events)}[/]")

    while running:
        _animated_sleep(
            poll_interval,
            _ANIM_ACTIVE if active else _ANIM_IDLE,
            stop_check=lambda: not running,
        )

        if not running:
            break

        # Check JSONL directory for activity
        if not jsonl_dir.is_dir():
            continue

        jsonl_files = list(jsonl_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        # Detect activity across ALL jsonl files
        max_mtime = max(f.stat().st_mtime for f in jsonl_files)

        if max_mtime > last_activity:
            if not active:
                ts = time.strftime("%H:%M")
                _console.print(f"  [dim]└[{ts}][/dim] [bold #f7e08a]✦[/]  active")
                active = True
            last_activity = max_mtime

            # Live recall: check topic drift on each new activity burst
            try:
                _update_recall_if_drifted(jsonl_files)
            except Exception as e:
                if config.debug_mode:
                    print(f"  [recall] Error: {e}", file=sys.stderr)

            continue

        # Check idle timeout
        if active and (time.time() - last_activity) > idle_timeout:
            ts = time.strftime("%H:%M")
            _console.print(f"  [dim]└[{ts}][/dim] [bold #f7a8d0]⟳[/]  processing...")

            # Process ALL jsonl files with new content
            cursor = _load_cursor(code_path)
            total_turns: list[dict[str, str]] = []

            for jf in sorted(jsonl_files, key=lambda p: p.stat().st_mtime):
                jkey = str(jf.resolve())
                entry = cursor.get(jkey, {"byte_offset": 0, "mtime": 0.0})

                jf_mtime = jf.stat().st_mtime
                if jf_mtime < entry["mtime"]:
                    entry["byte_offset"] = 0

                turns, new_offset = _parse_jsonl_from(jf, entry["byte_offset"])
                if turns:
                    total_turns.extend(turns)
                cursor[jkey] = {"byte_offset": new_offset, "mtime": jf_mtime}

            if total_turns:
                try:
                    r = post_session(config, total_turns)
                    _console.print(f"  [bold #a8f0e8]+{r['events_written']}[/] memories")
                except Exception as e:
                    _console.print(f"  [red]error:[/] {e}")

                # Refresh recall with latest retrieval
                try:
                    store = HomeStore(config)
                    from fiam.retriever import joint as joint_retriever
                    events = joint_retriever.search("", store, config)
                    _write_recall(config, events)
                    recall_query_vec = None  # reset drift baseline
                    _console.print(f"  recall [#f7a8d0]←[/] [bold #f7e08a]{len(events)}[/] fragments")
                except Exception as e:
                    _console.print(f"  [red]recall error:[/] {e}")
            else:
                _console.print(f"  [dim]·  up to date[/dim]")

            _save_cursor(code_path, cursor)
            active = False

    # Cleanup
    pid_file.unlink(missing_ok=True)
    _console.print()
    _console.print(_flow("  ( ˘ω˘ )  see you"))
    _console.print()


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop a running daemon."""
    code_path = _project_root()
    pid = _is_daemon_running(code_path)
    if pid is None:
        print("fiam is not running.")
        return

    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGTERM)

    _pid_path(code_path).unlink(missing_ok=True)
    print(f"fiam stopped (PID {pid}).")


def cmd_status(args: argparse.Namespace) -> None:
    """Show daemon status and memory stats."""
    code_path = _project_root()
    toml = _toml_path()

    pid = _is_daemon_running(code_path)
    if pid:
        print(f"  fiam: running (PID {pid})")
    else:
        print(f"  fiam: stopped")

    if toml.exists():
        from fiam.config import FiamConfig
        config = FiamConfig.from_toml(toml, code_path)
        print(f"  home: {config.home_path}")

        events_dir = config.events_dir
        if events_dir.is_dir():
            count = len(list(events_dir.glob("*.md")))
            print(f"  events: {count}")

        emb_dir = config.embeddings_dir
        if emb_dir.is_dir():
            count = len(list(emb_dir.glob("*.npy")))
            print(f"  embeddings: {count}")

        cursor = _load_cursor(code_path)
        if cursor:
            latest_mtime = max(v.get("mtime", 0) for v in cursor.values())
            if latest_mtime > 0:
                from datetime import datetime
                dt = datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"  last processed: {dt}")
    else:
        print("  (no fiam.toml — run 'fiam init')")


def cmd_reindex(args: argparse.Namespace) -> None:
    """Rebuild all embeddings with current models."""
    config = _build_config(args)
    from fiam.retriever.embedder import Embedder
    from fiam.store.home import HomeStore

    store = HomeStore(config)
    embedder = Embedder(config)
    events = store.all_events()

    if not events:
        print("No events to reindex.")
        return

    print(f"Reindexing {len(events)} events...")
    for i, event in enumerate(events, 1):
        if not event.body.strip():
            continue
        vec = embedder.embed(event.body)
        emb_path = embedder.save(vec, event.event_id)
        event.embedding = emb_path
        event.embedding_dim = vec.shape[-1]
        store.update_metadata(event)
        if config.debug_mode or i % 10 == 0:
            print(f"  [{i}/{len(events)}] {event.filename}")

    print(f"Done. All embeddings are now {config.embedding_dim}-dim.")

def cmd_pre(args: argparse.Namespace) -> None:
    """Run pre_session pipeline."""
    config = _build_config(args)
    from fiam.pipeline import pre_session

    result = pre_session(config)
    print(f"pre_session complete (session: {result['session_id']})")
    print(f"  events in home: {result['event_count']}")
    print(f"  background written to: {config.background_path}")


def cmd_post(args: argparse.Namespace) -> None:
    """Run post_session pipeline on the latest JSONL or a test file."""
    config = _build_config(args)
    from fiam.pipeline import post_session

    test_file = getattr(args, "test_file", None)
    if test_file:
        test_path = Path(test_file).resolve()
        if not test_path.exists():
            print(f"Error: test file not found: {test_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Loading test fixture: {test_path}")
        conversation = json.loads(test_path.read_text(encoding="utf-8"))
    else:
        jsonl_path = _find_latest_jsonl(config.home_path, debug=config.debug_mode)
        if jsonl_path is None:
            print("Error: no JSONL session file found for this home.", file=sys.stderr)
            print("Looked in:", _claude_projects_dir(), file=sys.stderr)
            sys.exit(1)

        force = getattr(args, "force", False)
        jkey = str(jsonl_path.resolve())
        cursor = _load_cursor(config.code_path)
        entry = cursor.get(jkey, {"byte_offset": 0, "mtime": 0.0})

        if not force and entry["byte_offset"] > 0:
            # Check if there's new content
            current_size = jsonl_path.stat().st_size
            if entry["byte_offset"] >= current_size:
                print(f"Already fully processed: {jsonl_path}")
                print("  Use --force to reprocess from start.")
                return

        if force:
            entry["byte_offset"] = 0

        print(f"Processing: {jsonl_path} (offset {entry['byte_offset']})")
        conversation, new_offset = _parse_jsonl_from(jsonl_path, entry["byte_offset"])

        if conversation:
            # Update cursor after successful processing
            cursor[jkey] = {"byte_offset": new_offset, "mtime": jsonl_path.stat().st_mtime}
            _save_cursor(config.code_path, cursor)

    if not conversation:
        print("Warning: no conversation turns found.", file=sys.stderr)
        sys.exit(1)

    print(f"  {len(conversation)} turns parsed")
    result = post_session(config, conversation)
    print(f"post_session complete (session: {result['session_id']})")
    print(f"  events written: {result['events_written']}")
    print(f"  report: {result['report_path']}")


def cmd_session(args: argparse.Namespace) -> None:
    """Legacy: pre → claude → post. Use 'fiam start' instead."""
    config = _build_config(args)
    from fiam.pipeline import pre_session, post_session

    pre_result = pre_session(config)
    print(f"Background ready: {config.background_path}\n")

    try:
        subprocess.run(["claude"], cwd=str(config.home_path), check=False)
    except FileNotFoundError:
        print("Error: 'claude' not found in PATH.", file=sys.stderr)
        sys.exit(1)

    jsonl_path = _find_latest_jsonl(config.home_path, debug=config.debug_mode)
    if jsonl_path is None:
        print("Warning: no JSONL found. Skipping post.", file=sys.stderr)
        return

    conversation = _parse_jsonl(jsonl_path)
    if not conversation:
        print("Warning: no turns found. Skipping post.", file=sys.stderr)
        return

    result = post_session(config, conversation, session_id=pre_result["session_id"])
    print(f"Done: {result['events_written']} events, report: {result['report_path']}")


def cmd_find_sessions(args: argparse.Namespace) -> None:
    """Diagnostic: list all JSONL session files found under ~/.claude/projects/."""
    home_path = Path(args.home).resolve()
    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(home_path)
    expected_dir = projects_dir / sanitized

    print(f"Home path:        {home_path}")
    print(f"Projects dir:     {projects_dir}")
    print(f"Sanitized name:   {sanitized}")
    print(f"Expected dir:     {expected_dir}")
    print(f"Expected exists:  {expected_dir.is_dir()}")
    print()

    if not projects_dir.is_dir():
        print(f"Projects directory does not exist: {projects_dir}")
        return

    # List all subdirectories and their JSONL files
    found_any = False
    for subdir in sorted(projects_dir.iterdir()):
        if not subdir.is_dir():
            continue
        jsonl_files = sorted(subdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not jsonl_files:
            continue
        found_any = True
        marker = " ← MATCH" if subdir.name == sanitized else ""
        print(f"  {subdir.name}/{marker}")
        for jf in jsonl_files:
            size_kb = jf.stat().st_size / 1024
            from datetime import datetime
            mtime = datetime.fromtimestamp(jf.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {jf.name}  ({size_kb:.1f} KB, modified {mtime})")

    if not found_any:
        print("  (no JSONL files found in any project directory)")


def cmd_scan(args: argparse.Namespace) -> None:
    """One-time scan: process all historical JSONL files into memory."""
    config = _build_config(args)
    from fiam.pipeline import post_session

    projects_dir = _claude_projects_dir()
    sanitized = _sanitize_home_path(config.home_path)
    jsonl_dir = projects_dir / sanitized

    if not jsonl_dir.is_dir():
        print(f"No project directory found: {jsonl_dir}", file=sys.stderr)
        print("Open Claude Code in your home directory first.", file=sys.stderr)
        sys.exit(1)

    jsonl_files = sorted(jsonl_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not jsonl_files:
        print("No JSONL session files found.")
        return

    print(f"\n  Scanning {len(jsonl_files)} session file(s)...\n")

    cursor = _load_cursor(config.code_path)
    total_events = 0

    for i, jf in enumerate(jsonl_files, 1):
        turns, new_offset = _parse_jsonl_from(jf, 0)
        if not turns:
            print(f"  [{i}/{len(jsonl_files)}] {jf.name}: 0 turns, skipped")
            continue
        print(f"  [{i}/{len(jsonl_files)}] {jf.name}: {len(turns)} turns", end="", flush=True)
        try:
            r = post_session(config, turns)
            n = r["events_written"]
            total_events += n
            print(f" → {n} events")
        except Exception as e:
            print(f" → error: {e}")
        jkey = str(jf.resolve())
        cursor[jkey] = {"byte_offset": new_offset, "mtime": jf.stat().st_mtime}

    _save_cursor(config.code_path, cursor)
    print(f"\n  Done. {total_events} events stored.")
    print(f"  Run 'fiam start' to begin live tracking.\n")


# ------------------------------------------------------------------
# clean — reset store to factory-fresh state
# ------------------------------------------------------------------

def cmd_clean(args: argparse.Namespace) -> None:
    """Reset store to factory-fresh state (events, embeddings, logs)."""
    import shutil

    code_path = _project_root()

    # Refuse if daemon is running
    pid = _is_daemon_running(code_path)
    if pid:
        print(f"  Error: daemon is running (PID {pid}). Run 'fiam stop' first.",
              file=sys.stderr)
        sys.exit(1)

    store_dir = code_path / "store"
    logs_sessions = code_path / "logs" / "sessions"

    def _count(path: Path) -> int:
        if path.is_dir():
            return sum(1 for _ in path.rglob("*") if _.is_file())
        return 1 if path.exists() else 0

    # Build list of things to wipe
    targets: list[tuple[Path, str]] = []
    for label, path in [
        ("events",     store_dir / "events"),
        ("embeddings", store_dir / "embeddings"),
        ("graph",      store_dir / "graph"),
        ("sessions",   logs_sessions),
    ]:
        n = _count(path)
        if n:
            s = "s" if n != 1 else ""
            targets.append((path, f"{n} file{s} ({label})"))

    for label, path in [
        ("cursor",    store_dir / "cursor.json"),
        ("cache",     store_dir / "narrative_cache.json"),
    ]:
        if path.exists():
            targets.append((path, label))

    # Check recall.md in home
    recall_path: Path | None = None
    toml = _toml_path()
    if toml.exists():
        try:
            from fiam.config import FiamConfig
            cfg = FiamConfig.from_toml(toml, code_path)
            if cfg.background_path.exists():
                recall_path = cfg.background_path
        except Exception:
            pass

    if not targets and recall_path is None:
        print()
        print("  Already clean — nothing to remove.")
        print()
        return

    print()
    print("  fiam clean")
    print()
    for path, label in targets:
        try:
            rel = path.relative_to(code_path)
        except ValueError:
            rel = path
        print(f"  {label:<32}  {rel}")
    if recall_path:
        print(f"  {'recall.md':<32}  {recall_path}")
    print()

    if not getattr(args, "yes", False):
        confirm = input("  Proceed? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            print()
            return

    # Execute
    for path, _ in targets:
        if path.is_dir():
            shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        elif path.exists():
            path.unlink()

    if recall_path and recall_path.exists():
        recall_path.unlink()

    print()
    print("  Done. fiam is clean. Run 'fiam scan' or 'fiam start' to begin.")
    print()


# ------------------------------------------------------------------
# graph — Obsidian wikilink graph (1.0 easter egg 🎉)
# ------------------------------------------------------------------

# Word extraction patterns — variable length, maximum chaos
_GRAPH_CJK_WORD = re.compile(r"[\u4e00-\u9fff]{1,7}")   # 1–7 CJK chars
_GRAPH_EN_WORD  = re.compile(r"\b[a-zA-Z]{3,7}\b")       # 3–7 letter words (punchy)
_GRAPH_BORING_EN = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "her", "was", "one", "our", "out", "day", "get", "has", "him",
    "his", "how", "its", "let", "man", "new", "now", "old", "see",
    "two", "way", "who", "boy", "did", "etc", "use",
    "this", "that", "with", "from", "have", "been", "will", "what",
    "when", "just", "your", "they", "them", "then", "than", "some",
    "were", "each", "more", "also", "like", "into", "very", "much",
    "here", "there", "would", "could", "should", "about", "which",
    "their", "other", "after", "before", "because", "through",
    "these", "those", "being", "doing", "having", "started", "ended",
    "user", "assistant",
}
_GRAPH_BORING_ZH = {
    "的是", "不是", "可以", "我们", "你们", "他们", "这个", "那个",
    "什么", "怎么", "的", "了", "是", "在", "我", "你", "他", "她",
    "它", "也", "都", "不", "没", "有", "就", "和", "与", "或",
}


def _graph_candidates(body: str) -> list[str]:
    """Return a shuffled list of interesting word candidates from event body."""
    import random
    clean = re.sub(r'\[(?:user|assistant)\]\s*', '', body).strip()

    zh = [w for w in _GRAPH_CJK_WORD.findall(clean) if w not in _GRAPH_BORING_ZH]
    en = [w for w in _GRAPH_EN_WORD.findall(clean) if w.lower() not in _GRAPH_BORING_EN]

    # Deduplicate while preserving order, then shuffle for chaos
    seen: set[str] = set()
    candidates: list[str] = []
    for w in zh + en:
        if w not in seen:
            seen.add(w)
            candidates.append(w)

    random.shuffle(candidates)
    return candidates


def _pick_name(body: str, used: set[str]) -> str:
    """Pick an unused name from this event's body; re-roll on collision.

    If every candidate in the body is already used, fall back to
    appending a number to the first good candidate.
    """
    candidates = _graph_candidates(body)

    for c in candidates:
        if c not in used:
            return c

    # All candidates taken — number-suffix the first one
    base = candidates[0] if candidates else "memory"
    i = 2
    while f"{base}{i}" in used:
        i += 1
    return f"{base}{i}"


def cmd_graph(args: argparse.Namespace) -> None:
    """Generate an Obsidian graph from the event store.

    Copies events to store/graph/ with human-readable names,
    adds [[wikilinks]] between events whose cosine similarity
    exceeds the threshold. Open the folder in Obsidian → graph view!
    """
    import shutil
    import numpy as np
    from fiam.store.home import HomeStore

    config = _build_config(args)
    store = HomeStore(config)
    events = store.all_events()

    if not events:
        print("No events in store. Run 'fiam post' first.")
        return

    threshold = getattr(args, "threshold", 0.75)

    # --- Phase 1: Name assignment ---
    used_names: set[str] = set()
    name_map: dict[str, str] = {}  # event_id → display name

    for ev in events:
        name = _pick_name(ev.body, used_names)
        used_names.add(name)
        name_map[ev.filename] = name

    # --- Phase 2: Copy files ---
    graph_dir = config.store_dir / "graph"
    if graph_dir.exists():
        shutil.rmtree(graph_dir)
    graph_dir.mkdir(parents=True)

    # Write clean Obsidian-friendly files
    for ev in events:
        name = name_map[ev.filename]
        # Strip role markers for cleaner display
        clean_body = re.sub(r'\[(?:user|assistant)\]\s*', '', ev.body).strip()

        lines = [
            f"*{ev.time.strftime('%Y-%m-%d %H:%M')}*",
            f"v={ev.valence:+.2f}  a={ev.arousal:.2f}",
            "",
            clean_body,
            "",
        ]
        (graph_dir / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")

    # --- Phase 3: Compute similarity & add wikilinks ---
    # Load embeddings
    vecs: dict[str, np.ndarray] = {}
    for ev in events:
        if ev.embedding:
            npy_path = config.store_dir / ev.embedding
            if npy_path.exists():
                vecs[ev.filename] = np.load(npy_path).astype(np.float32).flatten()

    # Pairwise cosine similarity → wikilinks
    links: dict[str, list[str]] = {ev.filename: [] for ev in events}
    ev_ids = [ev.filename for ev in events if ev.filename in vecs]
    link_count = 0

    for i, a in enumerate(ev_ids):
        for b in ev_ids[i + 1:]:
            va, vb = vecs[a], vecs[b]
            sim = float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))
            if sim >= threshold:
                links[a].append(b)
                links[b].append(a)
                link_count += 1

    # Append wikilinks to files
    for ev in events:
        neighbors = links.get(ev.filename, [])
        if not neighbors:
            continue
        name = name_map[ev.filename]
        path = graph_dir / f"{name}.md"
        wikilinks = " ".join(f"[[{name_map[n]}]]" for n in neighbors)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"---\n{wikilinks}\n")

    # --- Done! ---
    print()
    print(f"  🎉 fiam 1.0 — memory graph")
    print(f"  {len(events)} events → {graph_dir}")
    print(f"  {link_count} links (threshold ≥ {threshold})")
    print()
    print(f"  Open in Obsidian: {graph_dir}")
    print()

    # Preview
    for ev in events:
        name = name_map[ev.filename]
        n_links = len(links.get(ev.filename, []))
        link_str = f"  ({'·'.join(name_map[n] for n in links[ev.filename])})" if n_links else ""
        print(f"    {name}{link_str}")

    print()


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _setup_hf_cache() -> None:
    """Ensure HF_HOME points to project .cache/huggingface/ before any model loading."""
    code_path = _project_root()
    hf_home = code_path / ".cache" / "huggingface"
    os.environ["HF_HOME"] = str(hf_home)
    hf_home.mkdir(parents=True, exist_ok=True)


def main() -> None:
    # Setup HF_HOME to project cache before any model loading
    _setup_hf_cache()

    parser = argparse.ArgumentParser(
        prog="fiam",
        description="fiam — bio-inspired AI memory system",
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

    # init — no --home needed (interactive)
    sub_init = subparsers.add_parser("init", help="Interactive setup wizard")
    sub_init.set_defaults(func=cmd_init)

    # start
    sub_start = subparsers.add_parser("start", help="Start daemon (watch → slice → store)")
    add_common(sub_start)
    sub_start.set_defaults(func=cmd_start)

    # stop
    sub_stop = subparsers.add_parser("stop", help="Stop running daemon")
    sub_stop.set_defaults(func=cmd_stop)

    # status
    sub_status = subparsers.add_parser("status", help="Show daemon status and memory stats")
    sub_status.set_defaults(func=cmd_status)

    # reindex
    sub_reindex = subparsers.add_parser("reindex", help="Rebuild all embeddings")
    add_common(sub_reindex)
    sub_reindex.set_defaults(func=cmd_reindex)

    # pre (debug)
    sub_pre = subparsers.add_parser("pre", help="Run pre_session once (debug)")
    add_common(sub_pre)
    sub_pre.set_defaults(func=cmd_pre)

    # post (debug)
    sub_post = subparsers.add_parser("post", help="Run post_session once (debug)")
    add_common(sub_post)
    sub_post.add_argument("--test-file", type=str, default=None,
                          help="Path to a test fixture JSON")
    sub_post.add_argument("--force", action="store_true", default=False,
                          help="Reprocess from start of JSONL")
    sub_post.set_defaults(func=cmd_post)

    # session (legacy)
    sub_session = subparsers.add_parser("session", help="Legacy: pre → claude → post")
    add_common(sub_session)
    sub_session.set_defaults(func=cmd_session)

    # find-sessions (debug)
    sub_find = subparsers.add_parser("find-sessions", help="List JSONL files (debug)")
    add_common(sub_find)
    sub_find.set_defaults(func=cmd_find_sessions)

    # clean (reset store)
    sub_clean = subparsers.add_parser("clean", help="Reset store to factory-fresh state")
    sub_clean.add_argument("-y", "--yes", action="store_true", default=False,
                           help="Skip confirmation prompt")
    sub_clean.set_defaults(func=cmd_clean)

    # scan (one-time history import)
    sub_scan = subparsers.add_parser("scan", help="One-time scan of all JSONL history")
    add_common(sub_scan)
    sub_scan.set_defaults(func=cmd_scan)

    # graph (1.0 easter egg)
    sub_graph = subparsers.add_parser("graph", help="Generate Obsidian wikilink graph")
    add_common(sub_graph)
    sub_graph.add_argument("--threshold", type=float, default=0.75,
                           help="Cosine similarity threshold for wikilinks (default: 0.75)")
    sub_graph.set_defaults(func=cmd_graph)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
