# ISP Deployment — Test Plan
> April 12, 2026 | `feat/memory-graph` branch

## Status
- [x] ISP: repo cloned, Python 3.11, deps installed via uv
- [x] ISP: fiam.toml deployed, home dirs created (`/root/fiet-home/*`)
- [x] ISP: store uploaded (81 events, 81 embeddings)
- [x] ISP: env vars set (FIAM_TG_BOT_TOKEN, FIAM_EMAIL_PASSWORD)
- [x] GitHub: sync scripts committed & pushed

---

## Test A — Retrieval on ISP (AI runs)
> Graph + spreading activation on real data, no model download needed

**On ISP** (via SSH chain):
```bash
cd /root/fiam-code && source .venv/bin/activate
python -c "
from pathlib import Path
from fiam.config import FiamConfig
from fiam.store.home import HomeStore
from fiam.retriever import joint as jr

cfg = FiamConfig.from_toml(Path('fiam.toml'), Path('.'))
cfg.debug_mode = True
cfg.embedding_backend = 'local'  # will fail if no model, that's OK
store = HomeStore(cfg)

# Test: load all events, build graph, check links
events = store.all_events()
print(f'Events loaded: {len(events)}')

from fiam.retriever.graph import MemoryGraph
g = MemoryGraph()
g.build(events)
print(f'Graph: {g.G.number_of_nodes()} nodes, {g.G.number_of_edges()} edges')

# Test spreading activation from first event
if events:
    seed = {events[0].event_id: 1.0}
    activation = g.spread(seed)
    top3 = sorted(activation.items(), key=lambda x: -x[1])[:3]
    print(f'Spread from {events[0].event_id}: {top3}')
"
```
**Expected**: 81 nodes, ~3000+ edges, spreading activation shows decay across hops.

---

## Test B — Pre-session Injection (AI runs)
> Full pipeline minus embedding (uses pre-computed vectors)

**On ISP**:
```bash
cd /root/fiam-code && source .venv/bin/activate
# pre_session reads existing embeddings, no model download needed
python -m fiam pre --debug 2>&1 | head -60
```
**Possible issue**: Embedder may try to load model for query embedding.
If it fails on model download, that's expected on ISP (no GPU, 2GB RAM).
The test confirms: config loading, event retrieval, graph building, synthesis.

**Workaround** if model fails: set `embedding_backend = "remote"` + `embedding_remote_url` in fiam.toml to point to DO (once DO is set up with the embedding API server).

---

## Test C — TG Notification (AI runs)
> Test Telegram bot can send from ISP

**On ISP**:
```bash
cd /root/fiam-code && source .venv/bin/activate
python -c "
import os, sys
sys.path.insert(0, 'scripts')
from fiam_lib.postman import _tg_send
ok = _tg_send(
    token=os.environ['FIAM_TG_BOT_TOKEN'],
    chat_id='8629595965',
    text='🏠 ISP test: Fiet is online from home server.'
)
print('TG send:', ok)
"
```
**Expected**: Message appears in Telegram, function returns `True`.

---

## Test D — Email Send (AI runs)
> Verify Zoho SMTP works from ISP

**On ISP**:
```bash
cd /root/fiam-code && source .venv/bin/activate
python -c "
import os, sys
sys.path.insert(0, 'scripts')
from fiam_lib.postman import _email_send
ok = _email_send(
    smtp_host='smtppro.zoho.com',
    smtp_port=587,
    from_addr='fiet@fiet.cc',
    to_addr='fiet@fiet.cc',
    subject='ISP deployment test',
    body='Sent from ISP home server. Graph memory is live.',
    password=os.environ['FIAM_EMAIL_PASSWORD'],
)
print('Email send:', ok)
"
```
**Expected**: Email arrives at fiet@fiet.cc, function returns `True`.

---

## Test E — Post-session with Test Fixture (AI runs)
> Process a sample conversation, create events, verify link generation

**On ISP**:
```bash
cd /root/fiam-code && source .venv/bin/activate
python -m fiam post --test-file test_vault/fixtures/conversation.json --debug 2>&1 | head -80
```
**Expected**: Events extracted, temporal + semantic links generated, report written.
**Note**: May need model for embedding new events. If fails, confirms we need DO as compute node.

---

## Test F — Sync Round-trip (Zephyr runs from Local)
> Verify store sync works bidirectionally

**From Local (PowerShell)**:
```powershell
# 1. Check rsync is available
rsync --version

# 2. Dry-run sync (show what would transfer)
.\scripts\sync-store.ps1 -DryRun

# 3. If rsync not available on Windows, use manual SCP:
# Upload:
scp -r store/ root@209.38.69.231:/tmp/fiam-store-staging/
ssh root@209.38.69.231 "rsync -avz /tmp/fiam-store-staging/ root@99.173.22.93:/root/fiam-code/store/"

# Download (after ISP creates new events):
ssh root@209.38.69.231 "rsync -avz root@99.173.22.93:/root/fiam-code/store/ /tmp/fiam-store-staging/"
scp -r root@209.38.69.231:/tmp/fiam-store-staging/ store/
```

---

## Test G — SSH Tunnel (Zephyr runs, needs admin)
> Enable reverse tunnel so ISP can reach Local

**From Local (Admin PowerShell)**:
```powershell
# 1. Start sshd service
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic

# 2. Launch tunnel
.\scripts\fiam-tunnel.ps1
```

**Then from ISP** (verify):
```bash
ssh -p 2222 Aurora@127.0.0.1 "echo TUNNEL_OK"
```

---

## Execution Order

### AI runs these (on ISP, via SSH chain from Local terminal):
1. **Test A** — Graph retrieval ← start here, pure Python, no model needed
2. **Test C** — TG notification
3. **Test D** — Email send
4. **Test B** — Pre-session (may need model)
5. **Test E** — Post-session with fixture (may need model)

### Zephyr runs these (on Local):
6. **Test G** — SSH tunnel (needs admin PowerShell)
7. **Test F** — Sync round-trip

### Blocked until DO is deployed:
- Full pre_session with embedding query (needs remote embedding API on DO)
- End-to-end: daemon watches JSONL → extracts → embeds → retrieves → injects
