#!/usr/bin/env pwsh
# sync-store.ps1 — Bidirectional store sync between Local and ISP (via DO jump)
#
# Usage:
#   .\scripts\sync-store.ps1              # full bidirectional sync (default)
#   .\scripts\sync-store.ps1 -Direction up    # Local → ISP only
#   .\scripts\sync-store.ps1 -Direction down  # ISP → Local only
#   .\scripts\sync-store.ps1 -DryRun         # show what would transfer
#
# Topology: Local → DO (209.38.69.231) → ISP (99.173.22.93)
# Uses ssh ProxyJump through DO to reach ISP directly with rsync.
#
# What syncs:
#   store/events/     — event .md files (body immutable, links update)
#   store/embeddings/ — .npy vectors (immutable)
#   store/graph/      — any graph data
#
# What does NOT sync:
#   store/cursor.json — node-local daemon state
#   store/**/*.pid    — process locks
#
# Conflict strategy:
#   Events: newer file wins (rsync --update). Since link updates bump mtime,
#   whichever node last ran semantic/temporal linking has the authoritative links.
#   Embeddings: identical content per event_id, no real conflict possible.

param(
    [ValidateSet("both", "up", "down")]
    [string]$Direction = "both",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# --- Configuration ---
$DO = "root@209.38.69.231"
$ISP = "root@99.173.22.93"
$LOCAL_STORE = "f:/fiam-code/store/"
$REMOTE_STORE = "/root/fiam-code/store/"
$SSH_PROXY = "ssh -o ProxyJump=$DO"

# rsync base flags
$rsyncFlags = @("-avz", "--progress", "-e", "`"ssh -o ProxyJump=$DO`"")
$rsyncExclude = @("--exclude", "cursor.json", "--exclude", "*.pid")

if ($DryRun) {
    $rsyncFlags += "--dry-run"
    Write-Host "[sync] DRY RUN — no files will be transferred" -ForegroundColor Yellow
}

function Invoke-Sync {
    param([string]$Label, [string]$Src, [string]$Dst)
    Write-Host "`n[$Label] $Src → $Dst" -ForegroundColor Cyan
    $cmd = "rsync $($rsyncFlags -join ' ') $($rsyncExclude -join ' ') `"$Src`" `"$Dst`""
    Write-Host "  cmd: $cmd" -ForegroundColor DarkGray
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [$Label] FAILED (exit $LASTEXITCODE)" -ForegroundColor Red
        return $false
    }
    Write-Host "  [$Label] OK" -ForegroundColor Green
    return $true
}

$remote = "${ISP}:${REMOTE_STORE}"

# --- Sync ---
if ($Direction -eq "up" -or $Direction -eq "both") {
    Invoke-Sync "UP: Local→ISP" $LOCAL_STORE $remote
}

if ($Direction -eq "down" -or $Direction -eq "both") {
    Invoke-Sync "DOWN: ISP→Local" $remote $LOCAL_STORE
}

Write-Host "`n[sync] Done." -ForegroundColor Green
