#!/bin/bash
# fiam hook: UserPromptSubmit -> inject recall + external messages as additionalContext
#
# Two sources:
#   1. recall.md              -- memory fragments (surfaced by retrieval, NOT into flow.jsonl)
#   2. pending_external.txt   -- pre-formatted external messages (Conductor-prepared)
#
# External messages (TG/email) are already ingested into flow.jsonl by Conductor.
# This hook only handles CC delivery for interactive sessions.
# For non-interactive wakes, daemon delivers via `claude -p` user field directly.

HOME_DIR="$CLAUDE_PROJECT_DIR"
RECALL_FILE="$HOME_DIR/recall.md"
PENDING_FILE="$HOME_DIR/pending_external.txt"
PENDING_PROCESSING="$HOME_DIR/pending_external.processing"

PARTS=""

# ── 1. Recall ──
if [ -f "$RECALL_FILE" ] && [ -s "$RECALL_FILE" ]; then
    RECALL=$(sed 's/<!--.*-->//g' "$RECALL_FILE" | tr -s '\n' | sed '/^$/d')
    if [ -n "$RECALL" ]; then
        PARTS="[recall]\n${RECALL}"
    fi
fi

# ── 2. External messages (Conductor-prepared, pre-formatted) ──
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
