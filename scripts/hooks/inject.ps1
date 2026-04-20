# fiam hook: UserPromptSubmit -> inject self + recall + external as additionalContext
# Order (cache-optimized): [self] → [recall] → [external]

$homeDir = $env:CLAUDE_PROJECT_DIR
$selfDir = Join-Path $homeDir "self"
$recallFile = Join-Path $homeDir "recall.md"
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

# ── 2. Recall ──
if (Test-Path $recallFile) {
    $content = Get-Content $recallFile -Raw -ErrorAction SilentlyContinue
    if ($content -and $content.Trim().Length -gt 0) {
        $clean = $content -replace '<!--.*?-->', '' | ForEach-Object { $_.Trim() }
        if ($clean.Length -gt 0) {
            $parts += "[recall]`n$clean"
        }
    }
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
