# fiam — Fluid Injected Affective Memory

Long-term memory system for AI agents. Runs alongside Claude Code — watches conversation sessions, segments events in real-time, builds a memory graph with typed edges, and injects relevant memories via spreading activation. So the AI actually remembers.

## Architecture (v2 — Session 14)

```
Claude Code session
       │
       ├── JSONL log ──► Conductor ── Gorge (TextTiling) ──► Pool (events)
       │                     │                                    │
       │                     ├── drift detect ──► recall hook     ├── fingerprints.npy
       │                     ├── beat → flow.jsonl                ├── cosine.npy
       │                     └── embed (bge-m3)                   └── edges (PyG)
       │
       └── Hooks ◄──── inject recall as additionalContext
                 ├──── dispatch outbound messages (TG/email)
                 └──── boot summary on session start
```

### Core concepts

- **Beat** — atomic information unit in `flow.jsonl`. `{t, text, source, user_status, ai_status}`
- **Conductor** — info flow hub: beat ingestion → embed → Gorge segmentation → Pool storage → recall
- **Gorge** — TextTiling depth segmentation with peak-valley confirmation. Cuts beat stream into events in real-time
- **Pool** — unified 5-layer storage (replaces old scattered store/)
- **Spreading activation** — graph-based recall: seed → edge propagation → probabilistic selection (not top-k)

### Pool storage layers

| Layer | Format | Content |
|-------|--------|---------|
| Content | `events/<id>.md` | Event body text |
| Metadata | `events.jsonl` | `{id, t, access_count, fingerprint_idx}` |
| Fingerprints | `fingerprints.npy` | N × 1024 matrix (bge-m3) |
| Cosine | `cosine.npy` | N × N pairwise similarity |
| Edges | PyG `edge_index.npy` + `edge_attr.npy` | Typed directed edges (temporal/semantic/causal/remind/elaboration/contrast) |

### Beat sources

`cc` (dialogue) · `action` (tool use) · `tg` · `email` · `favilla` (mobile) · `schedule`

## Features

- **Real-time segment切分** — Gorge watches beat embedding stream, fires event cuts with TextTiling depth + confirm
- **Drift detection** — adjacent beat cosine below threshold → recall hook fires
- **Graph spreading activation** — seed from sliding vector, propagate along edges, weight multiplication, probabilistic fire
- **Multi-channel** — Telegram, email, Favilla (Android share intent), ActivityWatch
- **Web console** — SvelteKit 5 dashboard (Catppuccin dark), 3D force-directed graph with edge editing, event CRUD, flow viewer
- **Hook-mediated injection** — 4 CC hooks (UserPromptSubmit, Stop, SessionStart, PostCompact)
- **Lightweight deploy** — ML deps optional (`pip install -e ".[ml]"`); ISP runs without torch, embedding via remote API

## Install

```bash
git clone https://github.com/Zephyr-Ovo/Fiam.git && cd Fiam
uv sync                              # base deps (no torch)
uv sync --extra ml                   # with torch/transformers (for local embedding)
uv run python scripts/fiam.py init   # interactive setup wizard
uv run python scripts/fiam.py start  # start daemon
```

Requires [uv](https://astral.sh/uv) and [Claude Code](https://claude.ai/code).

For remote embedding (recommended): deploy `serve_embeddings.py` on a GPU server, set `embedding_backend = "remote"` in `fiam.toml`.

## Structure

```
src/fiam/
  config.py                # FiamConfig + fiam.toml parsing
  conductor.py          ★  # Beat ingestion → embed → gorge → pool → recall
  gorge.py              ★  # TextTiling depth segmentation (batch + streaming)
  store/
    beat.py             ★  # Beat dataclass + flow.jsonl I/O
    pool.py             ★  # Pool 5-layer storage (content/meta/fingerprints/cosine/edges)
  retriever/
    spread.py           ★  # Graph spreading activation (seed→spread→select)
    embedder.py            # Multi-profile embedder (local/remote)
  adapter/
    claude_code.py         # CC JSONL → Turn/Beat parsing
  pipeline.py              # Pre/post session orchestration
  classifier/              # Text intensity heuristic
  extractor/               # Event extraction (legacy TextTiling)

scripts/
  fiam.py                  # CLI: init, start, stop, scan, status
  dashboard_server.py      # Web console backend (Pool + legacy dual API)
  fiam_lib/
    daemon.py              # Main loop: poll, session management
    postman.py             # TG/email dispatch + inbox polling
    scheduler.py           # Scheduled tasks (wake cycles)

dashboard/                 # SvelteKit 5 + Svelte runes + Tailwind 4
  src/routes/graph/        # 3D force-directed graph (Canvas 2D)
  src/routes/events/       # Event list + detail
  src/routes/flow/         # Beat stream viewer
  src/lib/                 # API client, NodeEditor, EdgeMenu

developer/hooks/           # CC hook scripts
  inject.sh                # recall injection (UserPromptSubmit)
  outbox.sh                # outbound message extraction (Stop)
  boot.sh                  # daily summary (SessionStart)
  compact.sh               # archive summaries (PostCompact)
```

## Commands

| Command | Description |
|---------|-------------|
| `fiam init` | Interactive setup — creates `fiam.toml` |
| `fiam start` | Start daemon (monitors sessions, polls channels) |
| `fiam stop` | Graceful shutdown |
| `fiam scan` | One-time import of CC session history |
| `fiam status` | Show store counts + daemon state |

## Configuration

Copy `fiam.toml.example` → `fiam.toml` (or run `fiam init`).

Key settings:
- `embedding_backend`: `local` / `remote` — local HuggingFace or remote API
- `embedding_dim`: 1024 (bge-m3 default)
- `idle_timeout_minutes`: inactivity before post-session processing
- `tg_chat_id` / `email_*`: multi-channel settings
- `[conductor]` section: gorge window, confirm count, drift threshold

## License

MIT
