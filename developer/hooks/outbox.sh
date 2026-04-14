#!/bin/bash
# fiam hook: Stop → extract outbound message markers from assistant response
#
# The AI can include markers like:
#   [→tg:Zephyr] message text here
#   [→email:Zephyr] message text here
#
# This hook reads the stop_hook_data from stdin, extracts the last
# assistant message, finds markers, and writes outbox/.md files
# for dispatch by postman.
#
# IMPORTANT: This hook MUST output nothing to stdout (no hookSpecificOutput).
# Any stdout would cause CC to try to parse it as hook output.

HOME_DIR="$CLAUDE_PROJECT_DIR"
OUTBOX_DIR="$HOME_DIR/outbox"
mkdir -p "$OUTBOX_DIR"

# Read stdin (CC passes JSON with session info)
INPUT=$(cat)

# Extract the last assistant message text using Python
MSG=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    # Stop hook receives: {stopHookData: {transcript: [{role,content}]}}
    transcript = data.get('stopHookData', {}).get('transcript', [])
    # Find last assistant message
    for entry in reversed(transcript):
        if entry.get('role') == 'assistant':
            content = entry.get('content', '')
            if isinstance(content, list):
                parts = [b.get('text','') for b in content if isinstance(b,dict) and b.get('type')=='text']
                content = '\n'.join(parts)
            print(content)
            break
except Exception:
    pass
" 2>/dev/null)

if [ -z "$MSG" ]; then
    exit 0
fi

# Extract [→channel:recipient] blocks
echo "$MSG" | python3 -c "
import sys, re, os
from datetime import datetime

text = sys.stdin.read()
home = os.environ.get('CLAUDE_PROJECT_DIR', '.')
outbox = os.path.join(home, 'outbox')

# Pattern: [→channel:recipient] followed by text until next marker or end
pattern = r'\[→(tg|telegram|email):([^\]]+)\]\s*(.+?)(?=\[→(?:tg|telegram|email):|$)'
matches = re.findall(pattern, text, re.DOTALL)

for i, (channel, recipient, body) in enumerate(matches):
    body = body.strip()
    if not body:
        continue
    via = 'telegram' if channel in ('tg', 'telegram') else 'email'
    ts = datetime.now().strftime('%m%d_%H%M%S')
    fname = f'auto_{ts}_{i:02d}.md'
    content = f'---\nto: {recipient.strip()}\nvia: {via}\npriority: normal\n---\n\n{body}\n'
    with open(os.path.join(outbox, fname), 'w', encoding='utf-8') as f:
        f.write(content)
" 2>/dev/null

# Clean up interactive lock — Stop means session is ending
rm -f "$HOME_DIR/interactive.lock" 2>/dev/null

exit 0
