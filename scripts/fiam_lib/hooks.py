"""Hook templates and home-directory bootstrap helpers.

Used by init_wizard and home_mgmt to set up a new AI home.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fiam.config import FiamConfig


# ------------------------------------------------------------------
# Inject hook templates (fallback if scripts/hooks/ not available)
# ------------------------------------------------------------------

_INJECT_PS1_TEMPLATE = r'''# fiam hook: UserPromptSubmit -> inject recall as additionalContext
$recallFile = Join-Path $env:CLAUDE_PROJECT_DIR "recall.md"
if (Test-Path $recallFile) {
    $content = Get-Content $recallFile -Raw -ErrorAction SilentlyContinue
    if ($content -and $content.Trim().Length -gt 0) {
        $clean = $content -replace '<!--.*?-->', '' | ForEach-Object { $_.Trim() }
        if ($clean.Length -eq 0) { exit 0 }
        $escaped = $clean.Replace('\', '\\').Replace('"', '\"').Replace("`r`n", '\n').Replace("`n", '\n')
        Write-Output "{`"hookSpecificOutput`":{`"hookEventName`":`"UserPromptSubmit`",`"additionalContext`":`"$escaped`"}}"
        exit 0
    }
}
exit 0
'''

_INJECT_SH_TEMPLATE = r'''#!/bin/bash
# fiam hook: UserPromptSubmit -> inject recall + external as additionalContext
HOME_DIR="$CLAUDE_PROJECT_DIR"
RECALL_FILE="$HOME_DIR/recall.md"
PENDING_FILE="$HOME_DIR/pending_external.txt"
PENDING_PROCESSING="$HOME_DIR/pending_external.processing"
PARTS=""
if [ -f "$RECALL_FILE" ] && [ -s "$RECALL_FILE" ]; then
    RECALL=$(sed 's/<!--.*-->//g' "$RECALL_FILE" | tr -s '\n' | sed '/^$/d')
    if [ -n "$RECALL" ]; then
        PARTS="[recall]\n${RECALL}"
    fi
fi
if [ -f "$PENDING_FILE" ] && [ -s "$PENDING_FILE" ]; then
    mv "$PENDING_FILE" "$PENDING_PROCESSING" 2>/dev/null
    if [ -f "$PENDING_PROCESSING" ]; then
        EXTERNAL=$(cat "$PENDING_PROCESSING")
        if [ -n "$EXTERNAL" ]; then
            if [ -n "$PARTS" ]; then PARTS="${PARTS}\n\n"; fi
            PARTS="${PARTS}[external]\n${EXTERNAL}"
        fi
        ARCHIVE_DIR="$HOME_DIR/inbox/processed"
        mkdir -p "$ARCHIVE_DIR"
        mv "$PENDING_PROCESSING" "$ARCHIVE_DIR/external_$(date +%Y%m%d_%H%M%S).txt" 2>/dev/null
    fi
fi
if [ -n "$PARTS" ]; then
    ESCAPED=$(printf '%b' "$PARTS" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read())[1:-1])")
    echo "{\"hookSpecificOutput\":{\"hookEventName\":\"UserPromptSubmit\",\"additionalContext\":\"$ESCAPED\"}}"
fi
exit 0
'''


# ------------------------------------------------------------------
# Hook installation
# ------------------------------------------------------------------

# All hooks that should be installed (name, CC event, statusMessage)
_HOOKS_BASH = [
    ("inject.sh",  "UserPromptSubmit", "checking memory..."),
    ("boot.sh",    "SessionStart",     "waking up..."),
    ("outbox.sh",  "Stop",             "checking outbox..."),
    ("compact.sh", "PostCompact",      "saving context..."),
]

_HOOKS_WINDOWS = [
    ("inject.ps1", "UserPromptSubmit", "checking memory..."),
]


def install_hooks(config: "FiamConfig", platform: str) -> list[str]:
    """Install hook scripts and settings.local.json into home/.claude/.

    Copies hooks from code_path/scripts/hooks/ to home/.claude/hooks/.
    Returns list of installed hook paths (for display).
    """
    hooks_src = config.code_path / "scripts" / "hooks"
    hooks_dst = config.home_path / ".claude" / "hooks"
    hooks_dst.mkdir(parents=True, exist_ok=True)

    hook_defs = _HOOKS_WINDOWS if platform == "windows" else _HOOKS_BASH
    shell = "powershell" if platform == "windows" else "bash"
    installed = []

    for filename, _event, _msg in hook_defs:
        src = hooks_src / filename
        dst = hooks_dst / filename

        if dst.exists():
            continue  # never overwrite

        if src.exists():
            shutil.copy2(src, dst)
        else:
            # Fallback to embedded templates (inject only)
            if filename == "inject.ps1":
                dst.write_text(_INJECT_PS1_TEMPLATE, encoding="utf-8")
            elif filename == "inject.sh":
                dst.write_text(_INJECT_SH_TEMPLATE, encoding="utf-8")
            else:
                continue  # skip hooks we don't have a template for

        if platform != "windows" and filename.endswith(".sh"):
            dst.chmod(0o755)
        installed.append(str(dst))

    # Build settings.local.json with ALL hooks
    settings_path = config.home_path / ".claude" / "settings.local.json"
    if not settings_path.exists():
        hooks_config = {}
        for filename, event, msg in hook_defs:
            dst = hooks_dst / filename
            if not dst.exists():
                continue
            if platform == "windows":
                cmd = f'& "$env:CLAUDE_PROJECT_DIR\\.claude\\hooks\\{filename}"'
            else:
                cmd = f'"$CLAUDE_PROJECT_DIR/.claude/hooks/{filename}"'
            hooks_config[event] = [{"hooks": [{"type": "command", "command": cmd, "statusMessage": msg}]}]

        settings = {"hooks": hooks_config}
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        installed.append(str(settings_path))

    return installed


# ------------------------------------------------------------------
# CLAUDE.md bootstrap
# ------------------------------------------------------------------

def write_claude_md(config: "FiamConfig") -> bool:
    """Write CLAUDE.md to home from template. Returns False if already exists."""
    dest = config.claude_md_path
    if dest.exists():
        return False

    template_path = config.code_path / "scripts" / "templates" / "CLAUDE.md"
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
        # Interpolate identity
        content = content.replace("Fiet", config.ai_name, 1)
        content = content.replace("Zephyr", config.user_name)
    else:
        # Minimal fallback
        content = (
            f"你叫{config.ai_name}。\n"
            f"与你交谈的人叫{config.user_name}。\n\n"
            "这是你的家。\n"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return True


def write_gitignore(config: "FiamConfig") -> None:
    """Write a .gitignore for the AI home directory (skip if exists)."""
    dest = config.home_path / ".gitignore"
    if dest.exists():
        return

    lines = [
        "# fiam home — git-managed by the AI",
        "interactive.lock",
        "pending_external.txt",
        "pending_external.processing",
        "inbox/processed/",
        "outbox/sent/",
        ".claude/",
    ]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
