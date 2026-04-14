#!/bin/bash
# fiam hook: SessionStart → inject daily summary + manage interactive lock
#
# SessionStart receives JSON on stdin with a "source" field:
#   "startup"  — fresh `claude` launch
#   "resume"   — `claude --resume <id>`
#   "clear"    — after /clear
#   "compact"  — after auto-compact (but PostCompact is more specific)
#
# This hook:
#   1. Injects daily_summary.md as additionalContext (if it exists)
#   2. Writes interactive.lock if this is an interactive session (not -p mode)
#      The daemon checks this lock to avoid waking during human interaction.

HOME_DIR="$CLAUDE_PROJECT_DIR"
SELF_DIR="$HOME_DIR/self"
SUMMARY_FILE="$SELF_DIR/daily_summary.md"
LOCK_FILE="$HOME_DIR/interactive.lock"
ACTIVE_SESSION="$HOME_DIR/active_session.json"

# Read stdin
INPUT=$(cat)

# Parse source and session_id from hook input
eval "$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    print(f'SOURCE={json.dumps(data.get(\"source\", \"unknown\"))}')
    print(f'SESSION_ID={json.dumps(data.get(\"session_id\", \"\"))}')
except Exception:
    print('SOURCE=\"unknown\"')
    print('SESSION_ID=\"\"')
" 2>/dev/null)"

# Write interactive.lock for interactive sessions
# Daemon-sent messages use `claude -p` which does NOT trigger SessionStart,
# so any SessionStart is from a human (Zephyr) at the terminal.
# Lock includes PID so we can check if the session is still alive.
if [ "$SOURCE" = "startup" ] || [ "$SOURCE" = "resume" ]; then
    echo "{\"pid\":$$,\"source\":\"$SOURCE\",\"ts\":\"$(date -Iseconds)\"}" > "$LOCK_FILE"
fi

# Write active_session.json so the daemon knows which session to --resume
if [ -n "$SESSION_ID" ]; then
    echo "{\"session_id\":\"$SESSION_ID\",\"started_at\":\"$(date -Iseconds)\"}" > "$ACTIVE_SESSION"
fi

# Inject daily summary if present
if [ -f "$SUMMARY_FILE" ] && [ -s "$SUMMARY_FILE" ]; then
    CONTENT=$(cat "$SUMMARY_FILE")
    if [ -n "$CONTENT" ]; then
        ESCAPED=$(echo "$CONTENT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read())[1:-1])")
        echo "{\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"additionalContext\":\"$ESCAPED\"}}"
        exit 0
    fi
fi

exit 0
