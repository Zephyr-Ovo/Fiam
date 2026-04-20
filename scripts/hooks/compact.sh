#!/bin/bash
# fiam hook: PostCompact -> capture compact summary for continuity
#
# When CC auto-compacts a conversation, this hook receives the compact
# summary on stdin. We archive it so daily summaries and session context
# aren't lost.
#
# IMPORTANT: No stdout output (no hookSpecificOutput).

HOME_DIR="$CLAUDE_PROJECT_DIR"
SELF_DIR="$HOME_DIR/self"
COMPACT_DIR="$SELF_DIR/compact_history"
mkdir -p "$COMPACT_DIR"

# Read stdin (CC passes JSON with compact info)
INPUT=$(cat)

# Extract the compact summary
SUMMARY=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    # PostCompact receives: {compactSummary: '...'}
    summary = data.get('compactSummary', '')
    if not summary:
        # Try nested structure
        summary = data.get('postCompactHookData', {}).get('compactSummary', '')
    print(summary)
except Exception:
    pass
" 2>/dev/null)

if [ -z "$SUMMARY" ]; then
    exit 0
fi

# Write compact summary with timestamp
TS=$(date +%Y%m%d_%H%M%S)
DEST="$COMPACT_DIR/compact_${TS}.md"

cat > "$DEST" << EOF
# Compact Summary -- $(date '+%Y-%m-%d %H:%M')

$SUMMARY
EOF

# Also update daily_summary.md with a note about compaction
DAILY="$SELF_DIR/daily_summary.md"
if [ -f "$DAILY" ]; then
    echo "" >> "$DAILY"
    echo "---" >> "$DAILY"
    echo "_[auto-compact at $(date '+%H:%M')]_ Context was compacted." >> "$DAILY"
fi

exit 0
