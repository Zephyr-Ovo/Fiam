import json
from collections import Counter

edges = [json.loads(l) for l in open("store/graph.jsonl")]
c = Counter(e.get("type","?") for e in edges)
for t,n in c.most_common():
    print(f"  {t}: {n}")
print(f"Total: {len(edges)}")

print("\n=== Sample events ===")
import os
evts = sorted(os.listdir("store/events"))
for f in evts[:10]:
    txt = open(f"store/events/{f}").read()
    lines = txt.strip().split("\n")
    title = lines[0] if lines else "?"
    print(f"\n--- {f} ---")
    print(title)
    # print first 3 non-empty content lines
    content_lines = [l for l in lines[1:] if l.strip()][:3]
    for l in content_lines:
        print(l[:120])

print("\n=== Newest events (ev_0414) ===")
for f in evts:
    if "0414" in f:
        txt = open(f"store/events/{f}").read()
        print(f"\n--- {f} ---")
        print(txt[:300])
