#!/bin/bash
# fiam hook: UserPromptSubmit -> inject one-shot recall + external as additionalContext
#
# Injection order (cache-friendly: semi-static -> dynamic):
#   1. pending_recall.md  -- one-turn memory fragments
#   2. pending_external.txt -- external messages (changes per-message)
#
# self/*.md (identity / awareness / etc.) is NOT injected here. The dashboard
# already passes self/*.md to CC via --append-system-prompt (assembled by
# build_plain_prompt_parts -> load_self_context). Doing it here too caused
# a double injection of identity material into every CC turn.

HOME_DIR="$CLAUDE_PROJECT_DIR"
PENDING_RECALL="$HOME_DIR/pending_recall.md"
PENDING_RECALL_PROCESSING="$HOME_DIR/pending_recall.processing"
PENDING_FILE="$HOME_DIR/pending_external.txt"
PENDING_PROCESSING="$HOME_DIR/pending_external.processing"

PARTS=""

# ── 1. Recall (one-shot pending handoff) ──
if [ -f "$PENDING_RECALL" ] && [ -s "$PENDING_RECALL" ]; then
    mv "$PENDING_RECALL" "$PENDING_RECALL_PROCESSING" 2>/dev/null
    RECALL=$(sed 's/<!--.*-->//g' "$PENDING_RECALL_PROCESSING" | tr -s '\n' | sed '/^$/d')
    if [ -n "$RECALL" ]; then
        if [ -n "$PARTS" ]; then
            PARTS="${PARTS}\n\n"
        fi
        PARTS="${PARTS}[recall]\n${RECALL}"
    fi
    rm -f "$PENDING_RECALL_PROCESSING"
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
