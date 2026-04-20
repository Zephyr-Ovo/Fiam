"""Inspect copilot session JSONL structure."""
import json, sys

f = sys.argv[1] if len(sys.argv) > 1 else "F:/fiam-code/docs/copilot_session/0415_1045.jsonl"

with open(f, encoding="utf-8") as fh:
    for i, line in enumerate(fh):
        obj = json.loads(line)
        kind = obj.get("kind")
        
        if kind == 1:
            keys = obj.get("k", [])
            if "inputText" in keys:
                val = obj.get("v", "")
                print(f"[kind=1] inputText update: {str(val)[:150]}")
                print()
        
        if kind == 2:
            items = obj.get("v", [])
            for item in items:
                ts = item.get("timestamp")
                msg = item.get("message", {})
                msg_text = msg.get("text", "")
                resp = item.get("response", [])
                resp_kinds = [r.get("kind") for r in resp]
                
                # Find text content in response
                text_parts = [r for r in resp if r.get("kind") in ("text", "markdownContent")]
                
                print(f"=== Line {i}, ts={ts} ===")
                print(f"  msg_text len: {len(msg_text)}")
                if msg_text:
                    print(f"  msg_text[:300]: {msg_text[:300]}")
                print(f"  resp kinds: {resp_kinds}")
                print(f"  text parts count: {len(text_parts)}")
                for tp in text_parts:
                    v = tp.get("value", "")
                    print(f"  text part ({tp.get('kind')}): {v[:200]}")
                print()
