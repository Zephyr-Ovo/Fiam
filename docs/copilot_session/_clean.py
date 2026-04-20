"""
Clean copilot session JSONL files into readable conversation logs.

Output format per file: Markdown with timestamped user/AI turns.
Tool calls, thinking blocks, and metadata are stripped.
"""
import json, re, sys, os
from datetime import datetime, timezone, timedelta

PST = timezone(timedelta(hours=-7))

# Patterns to strip from AI response text (tool call renderings)
TOOL_PATTERNS = [
    # Tool invocation lines
    r'^Read memory \[.*?\]\(.*?\)\s*$',
    r'^Read \[.*?\]\(.*?\).*$',
    r'^Reading \[.*?\]\(.*?\).*$',
    r'^Searched for (?:text|regex) .*$',
    r'^Searching for (?:text|regex) .*$',
    r'^Ran .*$',
    r'^Running .*$',
    r'^Created .*$',
    r'^Edited .*$',
    r'^Listed directory .*$',
    r'^Found \d+ (?:file|result).*$',
    # Tool result counts
    r'^\d+ results?$',
    # File references with vscode URIs
    r'^\[.*?\]\(file:///.*?\)$',
    # Empty markdown links
    r'^\[?\]?\(file:///.*?\)$',
    # MCP/thinking artifacts
    r'^mcpServersStarting.*$',
    r'^Reviewed \d+ files?$',
    # Bare search result lines
    r'^`[^`]+`.*\d+ results?.*$',
]
TOOL_RE = [re.compile(p, re.MULTILINE) for p in TOOL_PATTERNS]


def clean_ai_text(text: str) -> str:
    """Remove tool call renderings from AI response text."""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Keep blank lines (paragraph breaks) but collapse multiples
            if cleaned and cleaned[-1] != '':
                cleaned.append('')
            continue
        # Check against tool patterns
        is_tool = False
        for pat in TOOL_RE:
            if pat.match(stripped):
                is_tool = True
                break
        if not is_tool:
            cleaned.append(line)
    
    # Collapse leading/trailing blanks
    while cleaned and cleaned[0] == '':
        cleaned.pop(0)
    while cleaned and cleaned[-1] == '':
        cleaned.pop()
    
    return '\n'.join(cleaned)


def ts_to_str(ts_ms):
    """Convert millisecond epoch to readable datetime string."""
    if not ts_ms:
        return None
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=PST)
    return dt.strftime('%Y-%m-%d %H:%M')


def process_file(path: str) -> list:
    """
    Process a JSONL session file into conversation turns.
    Returns list of {"time": str, "user": str, "ai": str}
    """
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()
    
    turns = []
    pending_input = None  # Track latest user input from kind=1 updates
    session_title = None
    
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        
        kind = obj.get('kind')
        
        # kind=0: session header - get initial input and title
        if kind == 0:
            v = obj.get('v', {})
            inp = v.get('inputState', {}).get('inputText', '')
            if inp and inp.strip():
                pending_input = inp.strip()
        
        # kind=1: field updates - track inputText changes and custom title
        if kind == 1:
            keys = obj.get('k', [])
            if 'inputText' in keys:
                val = obj.get('v', '')
                if isinstance(val, str) and val.strip():
                    pending_input = val.strip()
            if 'customTitle' in keys:
                session_title = obj.get('v', '')
        
        # kind=2: new requests appended
        if kind == 2:
            items = obj.get('v', [])
            for item in items:
                ts = item.get('timestamp')
                if not ts:
                    continue
                
                msg_text = item.get('message', {}).get('text', '')
                if not msg_text:
                    continue
                
                # Separate user input from AI response
                user_text = ''
                ai_text = msg_text
                
                if pending_input:
                    user_text = pending_input
                    # Try to strip user input prefix from msg_text
                    # msg_text often starts with user's input
                    prefix = pending_input[:80]
                    if msg_text.startswith(prefix):
                        ai_text = msg_text[len(pending_input):].lstrip('\r\n ')
                    pending_input = None
                
                # Clean AI text
                ai_text = clean_ai_text(ai_text)
                
                if user_text or ai_text:
                    turns.append({
                        'time': ts_to_str(ts),
                        'user': user_text,
                        'ai': ai_text,
                    })
    
    return turns, session_title


def write_markdown(turns, title, out_path):
    """Write conversation turns as clean markdown."""
    with open(out_path, 'w', encoding='utf-8') as f:
        if title:
            f.write(f'# {title}\n\n')
        else:
            f.write(f'# Session\n\n')
        
        for i, turn in enumerate(turns):
            f.write(f'## Turn {i+1}')
            if turn['time']:
                f.write(f'  ({turn["time"]})')
            f.write('\n\n')
            
            if turn['user']:
                f.write(f'**Zephyr:**\n\n{turn["user"]}\n\n')
            
            if turn['ai']:
                f.write(f'**Verso:**\n\n{turn["ai"]}\n\n')
            
            f.write('---\n\n')


def main():
    session_dir = sys.argv[1] if len(sys.argv) > 1 else 'F:/fiam-code/docs/copilot_session'
    
    files = sorted(f for f in os.listdir(session_dir) if f.endswith('.jsonl') and not f.startswith('_'))
    
    for fname in files:
        path = os.path.join(session_dir, fname)
        print(f'Processing {fname}...', end=' ')
        
        turns, title = process_file(path)
        if not turns:
            print(f'no turns, skipping')
            continue
        
        out_name = fname.replace('.jsonl', '.md')
        out_path = os.path.join(session_dir, out_name)
        write_markdown(turns, title, out_path)
        print(f'{len(turns)} turns -> {out_name}')


if __name__ == '__main__':
    main()
