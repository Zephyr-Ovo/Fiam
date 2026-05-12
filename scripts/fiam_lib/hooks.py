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

_INJECT_PS1_TEMPLATE = r'''# fiam hook: UserPromptSubmit -> inject self + recall + external as additionalContext
# Order (cache-optimized): [self] → [recall] → [external]

$homeDir = $env:CLAUDE_PROJECT_DIR
$selfDir = Join-Path $homeDir "self"
$recallFile = Join-Path $homeDir "recall.md"
$recallDirty = Join-Path $homeDir ".recall_dirty"
$pendingFile = Join-Path $homeDir "pending_external.txt"
$pendingProcessing = Join-Path $homeDir "pending_external.processing"

$parts = @()

# ── 1. Self (AI's identity — all .md files in self/) ──
if (Test-Path $selfDir) {
    $selfParts = @()
    Get-ChildItem -Path $selfDir -Filter "*.md" -File -ErrorAction SilentlyContinue | ForEach-Object {
        $content = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
        if ($content -and $content.Trim().Length -gt 0) {
            $name = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
            $selfParts += "# $name`n$($content.Trim())"
        }
    }
    if ($selfParts.Count -gt 0) {
        $parts += "[self]`n$($selfParts -join "`n")"
    }
}

# ── 2. Recall (only if .recall_dirty marker exists) ──
if ((Test-Path $recallDirty) -and (Test-Path $recallFile)) {
    $content = Get-Content $recallFile -Raw -ErrorAction SilentlyContinue
    if ($content -and $content.Trim().Length -gt 0) {
        $clean = $content -replace '<!--.*?-->', '' | ForEach-Object { $_.Trim() }
        if ($clean.Length -gt 0) {
            $parts += "[recall]`n$clean"
        }
    }
    Remove-Item $recallDirty -Force -ErrorAction SilentlyContinue
}

# ── 3. External messages ──
if (Test-Path $pendingFile) {
    $content = Get-Content $pendingFile -Raw -ErrorAction SilentlyContinue
    if ($content -and $content.Trim().Length -gt 0) {
        Move-Item $pendingFile $pendingProcessing -Force -ErrorAction SilentlyContinue
        if (Test-Path $pendingProcessing) {
            $external = Get-Content $pendingProcessing -Raw -ErrorAction SilentlyContinue
            if ($external -and $external.Trim().Length -gt 0) {
                $parts += "[external]`n$($external.Trim())"
            }
            $archiveDir = Join-Path $homeDir "inbox\processed"
            New-Item -ItemType Directory -Path $archiveDir -Force -ErrorAction SilentlyContinue | Out-Null
            $ts = Get-Date -Format "yyyyMMdd_HHmmss"
            Move-Item $pendingProcessing (Join-Path $archiveDir "external_$ts.txt") -Force -ErrorAction SilentlyContinue
        }
    }
}

# ── Output ──
if ($parts.Count -gt 0) {
    $joined = $parts -join "`n`n"
    $escaped = $joined.Replace('\', '\\').Replace('"', '\"').Replace("`r`n", '\n').Replace("`n", '\n')
    Write-Output "{`"hookSpecificOutput`":{`"hookEventName`":`"UserPromptSubmit`",`"additionalContext`":`"$escaped`"}}"
}

exit 0
'''

_INJECT_SH_TEMPLATE = r'''#!/bin/bash
# fiam hook: UserPromptSubmit -> inject self + recall + external as additionalContext
#
# Injection order (cache-optimized: static → semi-static → dynamic):
#   1. self/*.md          -- AI's identity/personality (AI-maintained, changes rarely)
#   2. recall.md          -- memory fragments (surfaced by retrieval, changes on drift)
#   3. pending_external.txt -- external messages (changes per-message)

HOME_DIR="$CLAUDE_PROJECT_DIR"
SELF_DIR="$HOME_DIR/self"
RECALL_FILE="$HOME_DIR/recall.md"
RECALL_DIRTY="$HOME_DIR/.recall_dirty"
PENDING_FILE="$HOME_DIR/pending_external.txt"
PENDING_PROCESSING="$HOME_DIR/pending_external.processing"

PARTS=""

# ── 1. Self (AI's identity — all .md files in self/, skip journal/) ──
if [ -d "$SELF_DIR" ]; then
    SELF_CONTENT=""
    for f in "$SELF_DIR"/*.md; do
        [ -f "$f" ] || continue
        [ -s "$f" ] || continue
        CONTENT=$(cat "$f")
        if [ -n "$CONTENT" ]; then
            FNAME=$(basename "$f")
            if [ -n "$SELF_CONTENT" ]; then
                SELF_CONTENT="${SELF_CONTENT}\n"
            fi
            SELF_CONTENT="${SELF_CONTENT}# ${FNAME%.md}\n${CONTENT}"
        fi
    done
    if [ -n "$SELF_CONTENT" ]; then
        PARTS="[self]\n${SELF_CONTENT}"
    fi
fi

# ── 2. Recall (only if .recall_dirty marker exists) ──
if [ -f "$RECALL_DIRTY" ] && [ -f "$RECALL_FILE" ] && [ -s "$RECALL_FILE" ]; then
    RECALL=$(sed 's/<!--.*-->//g' "$RECALL_FILE" | tr -s '\n' | sed '/^$/d')
    if [ -n "$RECALL" ]; then
        if [ -n "$PARTS" ]; then
            PARTS="${PARTS}\n\n"
        fi
        PARTS="${PARTS}[recall]\n${RECALL}"
    fi
    rm -f "$RECALL_DIRTY"
fi

# ── 3. External messages (Conductor-prepared, pre-formatted) ──
if [ -f "$PENDING_FILE" ] && [ -s "$PENDING_FILE" ]; then
    mv "$PENDING_FILE" "$PENDING_PROCESSING" 2>/dev/null
    if [ -f "$PENDING_PROCESSING" ]; then
        EXTERNAL=$(cat "$PENDING_PROCESSING")
        if [ -n "$EXTERNAL" ]; then
            if [ -n "$PARTS" ]; then
                PARTS="${PARTS}\n\n"
            fi
            PARTS="${PARTS}[external]\n${EXTERNAL}"
        fi

        # Archive
        ARCHIVE_DIR="$HOME_DIR/inbox/processed"
        mkdir -p "$ARCHIVE_DIR"
        mv "$PENDING_PROCESSING" "$ARCHIVE_DIR/external_$(date +%Y%m%d_%H%M%S).txt" 2>/dev/null
    fi
fi

# ── Output ──
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
# constitution.md bootstrap
# ------------------------------------------------------------------

def write_constitution_md(config: "FiamConfig") -> bool:
    """Write constitution.md to home from template. Returns False if exists.

    constitution.md is the fiam-owned system guide injected as system[0] by
    runtime/prompt.build_api_messages and via --append-system-prompt for CC.
    Claude Code does NOT auto-load it (that's the whole point of the rename
    away from CLAUDE.md), so fiam keeps full control over what reaches the
    model and there's no double-injection with CC's own auto-loader.
    """
    dest = config.constitution_md_path
    if dest.exists():
        return False

    template_path = config.code_path / "scripts" / "templates" / "constitution.md"
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
        if config.user_name:
            content = content.replace("Zephyr", config.user_name)
    else:
        # Minimal fallback
        user_line = f"与你交谈的人叫{config.user_name}。\n" if config.user_name else ""
        content = user_line + "\n这是你的家。\n"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return True


def write_awareness_md(config: "FiamConfig") -> bool:
    """Write self/awareness.md to home from template. Returns False if already exists.

    awareness.md is the API runtime's equivalent of CLAUDE.md: it teaches the AI
    about XML markers (<send>/<wake>/<todo at>/<sleep>/<state>/<hold>/COT)
    and other runtime conventions. prompt.load_self_context() picks it up automatically
    via the sorted-glob fallback.
    """
    dest = config.self_dir / "awareness.md"
    if dest.exists():
        return False

    template_path = config.code_path / "scripts" / "templates" / "awareness.md"
    if not template_path.exists():
        return False

    content = template_path.read_text(encoding="utf-8")
    if config.user_name:
        content = content.replace("Zephyr", config.user_name)

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
