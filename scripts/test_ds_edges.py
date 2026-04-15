#!/usr/bin/env python3
"""
Test DeepSeek edge typing on real events.

Picks 6 diverse events from store/, sends them to DeepSeek V3 with a prompt
asking it to decide which events are related and what the relationship type is.
This is a dry-run to validate prompt quality and cost before integrating.
"""

import json
import os
import sys
import time
from pathlib import Path

# --- Config ---
DS_API_KEY = os.environ.get("FIAM_GRAPH_API_KEY", "")
DS_BASE_URL = "https://api.deepseek.com"
DS_MODEL = "deepseek-chat"

# Events chosen for diversity: fiam开源, 饮食习惯, yak-shaving/SSH, Claude泄露, 拉丁文代码, 香农雕像
EVENT_IDS = ["ev_0408_010", "ev_0408_030", "ev_0408_050", "ev_0408_070", "ev_0408_075", "ev_0408_081"]

EDGE_PROMPT = """\
You are a memory graph edge classifier. Given a set of events from a person's life,
decide which pairs have meaningful relationships and classify the edge type.

## Edge types
- **cause**: A caused or led to B (directional: A → B)
- **remind**: A reminds of B or they share a pattern (bidirectional)
- **contrast**: A and B represent opposing experiences or changes
- **elaboration**: B adds detail or continues A's topic

## Rules
- Only create edges for pairs with GENUINE relationships. Most pairs should have NONE.
- Each edge needs a one-sentence reason (in the same language as the events).
- Output valid JSON array. Empty array if no meaningful edges.

## Events
{events_block}

## Output format
```json
[
  {{"from": "ev_id_1", "to": "ev_id_2", "type": "cause|remind|contrast|elaboration", "reason": "..."}}
]
```

Output ONLY the JSON array, no other text.
"""


def load_event_body(store_dir: Path, event_id: str) -> str:
    """Load event body text (after YAML frontmatter)."""
    path = store_dir / "events" / f"{event_id}.md"
    raw = path.read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()[:500]  # cap at 500 chars
    return raw[:500]


def load_event_meta(store_dir: Path, event_id: str) -> dict:
    """Load event time and metadata from frontmatter."""
    import yaml
    path = store_dir / "events" / f"{event_id}.md"
    raw = path.read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    if len(parts) >= 3:
        meta = yaml.safe_load(parts[1])
        return {
            "time": str(meta.get("time", "")),
            "valence": round(meta.get("valence", 0), 2),
            "arousal": round(meta.get("arousal", 0), 2),
        }
    return {}


def call_deepseek(prompt: str) -> tuple[str, dict]:
    """Call DeepSeek API and return (response_text, usage_dict)."""
    import urllib.request

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DS_API_KEY}",
    }
    body = json.dumps({
        "model": DS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1024,
    }).encode()

    req = urllib.request.Request(
        f"{DS_BASE_URL}/v1/chat/completions",
        data=body,
        headers=headers,
    )

    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    elapsed = time.time() - t0

    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return text, usage, elapsed


def main():
    if not DS_API_KEY:
        print("ERROR: FIAM_DS_API_KEY not set")
        sys.exit(1)

    # Determine store dir
    store_dir = Path(__file__).resolve().parent.parent / "store"
    if not store_dir.is_dir():
        print(f"ERROR: store not found at {store_dir}")
        sys.exit(1)

    # Load events
    events_block = ""
    for eid in EVENT_IDS:
        meta = load_event_meta(store_dir, eid)
        body = load_event_body(store_dir, eid)
        events_block += f"### {eid}\n"
        events_block += f"Time: {meta.get('time', '?')}  Valence: {meta.get('valence', '?')}  Arousal: {meta.get('arousal', '?')}\n"
        events_block += f"{body}\n\n"

    prompt = EDGE_PROMPT.format(events_block=events_block)

    print(f"=== DeepSeek Edge Typing Test ===")
    print(f"Events: {', '.join(EVENT_IDS)}")
    print(f"Prompt length: {len(prompt)} chars (~{len(prompt)//4} tokens)")
    print()

    # Call DS
    text, usage, elapsed = call_deepseek(prompt)

    print(f"--- Response ({elapsed:.1f}s) ---")
    print(text)
    print()
    print(f"--- Usage ---")
    print(f"  Input tokens:  {usage.get('prompt_tokens', '?')}")
    print(f"  Output tokens: {usage.get('completion_tokens', '?')}")
    in_cost = usage.get('prompt_tokens', 0) * 0.14 / 1_000_000
    out_cost = usage.get('completion_tokens', 0) * 0.28 / 1_000_000
    print(f"  Cost: ${in_cost + out_cost:.6f} (in: ${in_cost:.6f}, out: ${out_cost:.6f})")

    # Parse edges
    try:
        # Strip markdown code fences if present
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
        if clean.endswith("```"):
            clean = clean.rsplit("```", 1)[0]
        edges = json.loads(clean.strip())
        print(f"\n--- Parsed {len(edges)} edges ---")
        for e in edges:
            print(f"  {e['from']} --[{e['type']}]--> {e['to']}: {e['reason']}")
    except (json.JSONDecodeError, KeyError) as ex:
        print(f"\n--- Parse failed: {ex} ---")


if __name__ == "__main__":
    main()
