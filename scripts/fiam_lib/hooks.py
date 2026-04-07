"""Hook templates — auto-installed into home/.claude/ by fiam init."""

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
