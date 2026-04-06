# fiam hook: UserPromptSubmit → inject recall as additionalContext
# Reads recall.md from AI home; if non-empty, injects as emerging memories

$recallFile = Join-Path $env:CLAUDE_PROJECT_DIR "recall.md"

if (Test-Path $recallFile) {
    $content = Get-Content $recallFile -Raw -ErrorAction SilentlyContinue
    if ($content -and $content.Trim().Length -gt 0) {
        # Strip HTML comments (<!-- ... -->)
        $clean = $content -replace '<!--.*?-->', '' | ForEach-Object { $_.Trim() }
        if ($clean.Length -eq 0) {
            exit 0
        }
        # Escape for JSON
        $escaped = $clean.Replace('\', '\\').Replace('"', '\"').Replace("`r`n", '\n').Replace("`n", '\n')
        Write-Output "{`"hookSpecificOutput`":{`"hookEventName`":`"UserPromptSubmit`",`"additionalContext`":`"$escaped`"}}"
        exit 0
    }
}

# No recall — pass through silently
exit 0
