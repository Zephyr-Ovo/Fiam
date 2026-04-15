#!/bin/bash
# fiam hook: UserPromptSubmit -> inject recall + inbox as additionalContext
#
# Two sources:
#   1. recall.md       -- memory fragments (surfaced by retrieval)
#   2. inbox.jsonl     -- pending messages from TG/email (daemon writes these)
#
# Atomicity: inbox.jsonl is claimed via atomic `mv` to inbox.processing,
# so concurrent daemon writes never collide with hook reads.
#
# The output uses [recall] / [inbox] section markers so the JSONL adapter
# can distinguish them (recall MUST NOT enter fiam events -- anti-recursion).

HOME_DIR="$CLAUDE_PROJECT_DIR"
RECALL_FILE="$HOME_DIR/recall.md"
INBOX_FILE="$HOME_DIR/inbox.jsonl"
INBOX_PROCESSING="$HOME_DIR/inbox.processing"

PARTS=""

# ── 1. Recall ──
if [ -f "$RECALL_FILE" ] && [ -s "$RECALL_FILE" ]; then
    RECALL=$(sed 's/<!--.*-->//g' "$RECALL_FILE" | tr -s '\n' | sed '/^$/d')
    if [ -n "$RECALL" ]; then
        PARTS="[recall]\n${RECALL}"
    fi
fi

# ── 2. Inbox (atomic claim) ──
if [ -f "$INBOX_FILE" ] && [ -s "$INBOX_FILE" ]; then
    mv "$INBOX_FILE" "$INBOX_PROCESSING" 2>/dev/null
    if [ -f "$INBOX_PROCESSING" ]; then
        # Parse each JSONL line: extract from, via, body
        INBOX_TEXT=""
        while IFS= read -r line; do
            from=$(echo "$line" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('from','?'))" 2>/dev/null)
            via=$(echo "$line" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('via','?'))" 2>/dev/null)
            body=$(echo "$line" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('body',''))" 2>/dev/null)
            if [ -n "$body" ]; then
                INBOX_TEXT="${INBOX_TEXT}[${via}:${from}] ${body}\n"
            fi
        done < "$INBOX_PROCESSING"

        if [ -n "$INBOX_TEXT" ]; then
            if [ -n "$PARTS" ]; then
                PARTS="${PARTS}\n\n"
            fi
            PARTS="${PARTS}[inbox]\n${INBOX_TEXT}"
        fi

        # Archive processed inbox
        ARCHIVE_DIR="$HOME_DIR/inbox/processed"
        mkdir -p "$ARCHIVE_DIR"
        mv "$INBOX_PROCESSING" "$ARCHIVE_DIR/inbox_$(date +%Y%m%d_%H%M%S).jsonl" 2>/dev/null
    fi
fi

# ── Output ──
if [ -n "$PARTS" ]; then
    ESCAPED=$(printf '%b' "$PARTS" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read())[1:-1])")
    echo "{\"hookSpecificOutput\":{\"hookEventName\":\"UserPromptSubmit\",\"additionalContext\":\"$ESCAPED\"}}"
fi

exit 0
