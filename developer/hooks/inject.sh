#!/bin/bash
# fiam hook: UserPromptSubmit → inject recall as additionalContext
# Reads recall.md from AI home; if non-empty, injects as emerging memories

RECALL_FILE="$CLAUDE_PROJECT_DIR/recall.md"

if [ -f "$RECALL_FILE" ] && [ -s "$RECALL_FILE" ]; then
    # Strip HTML comments and check if content remains
    CONTENT=$(sed 's/<!--.*-->//g' "$RECALL_FILE" | tr -s '\n' | sed '/^$/d')
    if [ -n "$CONTENT" ]; then
        ESCAPED=$(echo "$CONTENT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read())[1:-1])")
        echo "{\"hookSpecificOutput\":{\"hookEventName\":\"UserPromptSubmit\",\"additionalContext\":\"$ESCAPED\"}}"
        exit 0
    fi
fi

# No recall — pass through silently
exit 0
