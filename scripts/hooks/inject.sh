#!/bin/bash
# fiam hook: UserPromptSubmit -> inject recall + carryover + external as additionalContext
#
# Injection order (cache-friendly: semi-static -> dynamic):
#   1. recall.md          -- memory fragments (surfaced by retrieval, changes on drift)
#   2. carryover.md       -- conversation turns from other runtimes (api) cc missed
#   3. pending_external.txt -- external messages (changes per-message)
#
# self/*.md (identity / awareness / etc.) is NOT injected here. The dashboard
# already passes self/*.md to CC via --append-system-prompt (assembled by
# build_plain_prompt_parts -> load_self_context). Doing it here too caused
# a double injection of identity material into every CC turn.

HOME_DIR="$CLAUDE_PROJECT_DIR"
RECALL_FILE="$HOME_DIR/recall.md"
RECALL_DIRTY="$HOME_DIR/.recall_dirty"
CARRYOVER_FILE="$HOME_DIR/carryover.md"
CARRYOVER_DIRTY="$HOME_DIR/.carryover_dirty"
PENDING_FILE="$HOME_DIR/pending_external.txt"
PENDING_PROCESSING="$HOME_DIR/pending_external.processing"

PARTS=""

# ── 1. Recall (only if .recall_dirty marker exists) ──
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

# ── 3. Carryover (turns cc missed while api/others answered) ──
if [ -f "$CARRYOVER_DIRTY" ] && [ -f "$CARRYOVER_FILE" ] && [ -s "$CARRYOVER_FILE" ]; then
    CARRYOVER=$(cat "$CARRYOVER_FILE")
    if [ -n "$CARRYOVER" ]; then
        if [ -n "$PARTS" ]; then
            PARTS="${PARTS}\n\n"
        fi
        PARTS="${PARTS}[carryover]\n${CARRYOVER}"
    fi
    : > "$CARRYOVER_FILE"
    rm -f "$CARRYOVER_DIRTY"
fi

# ── 4. External messages (Conductor-prepared, pre-formatted) ──
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
