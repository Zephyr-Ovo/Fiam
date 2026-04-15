"""One-shot dedup: keep one event per unique body, remove duplicates."""
import os
import hashlib
import collections

evdir = "store/events"
embdir = "store/embeddings"
files = sorted(f for f in os.listdir(evdir) if f.endswith(".md"))

# Group by body content hash
hash_to_files: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
for f in files:
    path = os.path.join(evdir, f)
    with open(path, "r") as fh:
        content = fh.read()
    parts = content.split("---", 2)
    body = parts[2].strip() if len(parts) >= 3 else content
    h = hashlib.md5(body.encode()).hexdigest()[:16]

    emb = ""
    if len(parts) >= 2:
        for line in parts[1].split("\n"):
            if line.startswith("embedding:"):
                emb = line.split(":", 1)[1].strip()

    hash_to_files[h].append((f, emb))

# Keep best file per group (prefer named > ev_MMDD), remove rest
to_remove_events = []
to_remove_embeddings = []
kept = []

for h, items in hash_to_files.items():
    named = [(f, e) for f, e in items if not f.startswith("ev_")]
    evnum = [(f, e) for f, e in items if f.startswith("ev_")]

    if named:
        keep = named[0]
        remove = named[1:] + evnum
    else:
        keep = evnum[0]
        remove = evnum[1:]

    kept.append(keep[0])
    for f, emb in remove:
        to_remove_events.append(f)
        if emb:
            to_remove_embeddings.append(emb)

print(f"Unique groups: {len(hash_to_files)}")
print(f"Keeping: {len(kept)}")
print(f"Removing: {len(to_remove_events)} events, {len(to_remove_embeddings)} embeddings")
print()

removed_ev = 0
removed_emb = 0
for f in to_remove_events:
    path = os.path.join(evdir, f)
    if os.path.exists(path):
        os.remove(path)
        removed_ev += 1

for emb in to_remove_embeddings:
    path = os.path.join("store", emb)
    if os.path.exists(path):
        os.remove(path)
        removed_emb += 1

remaining_ev = len([f for f in os.listdir(evdir) if f.endswith(".md")])
remaining_emb = len([f for f in os.listdir(embdir) if f.endswith(".npy")])

print(f"Removed {removed_ev} event files")
print(f"Removed {removed_emb} embedding files")
print(f"Remaining events: {remaining_ev}")
print(f"Remaining embeddings: {remaining_emb}")
print()
print("Kept files:")
for f in sorted(kept):
    print(f"  {f}")
