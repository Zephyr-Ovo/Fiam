# fiam — Fluid Injected Affective Memory

Long-term memory system for AI agents. Runs alongside Claude Code, records every information beat into an append-only flow, freezes bge-m3 vectors for training, and builds a typed memory graph through manual or automatic segmentation.

## Architecture (v2 — manual-first data collection)

```
Claude Code session
       │
  ├── JSONL log ──► Conductor ──► flow.jsonl + frozen beat vectors
  │                     │
  │                     ├── manual mode: dashboard cuts + DeepSeek edges
  │                     └── auto mode: drift + Gorge + Pool + recall
       │
       └── Hooks ◄──── inject recall as additionalContext
                 ├──── dispatch outbound messages (TG/email)
                 └──── boot summary on session start
```

### Core concepts

- **Beat** — atomic information unit in `flow.jsonl`. `{t, text, source, user_status, ai_status, meta?}`; embeddings and cuts use `text` only, while sender/url/route metadata lives in `meta`.
- **Conductor** — info flow hub: beat ingestion → flow persistence → frozen vector persistence → optional auto memory pipeline
- **FeatureStore** — frozen beat-level bge-m3 vectors in `store/features/`, keyed by beat hash for annotation/training
- **Gorge** — TextTiling depth segmentation with peak-valley confirmation. Used only in `memory_mode = "auto"`
- **Pool** — unified 5-layer storage (replaces old scattered store/)
- **Spreading activation** — graph-based recall: seed → edge propagation → probabilistic selection (not top-k)
- **Annotator** — dashboard workflow: human marks event/drift cuts, then DeepSeek proposes event names and graph edges for confirmation

### Pool storage layers

| Layer | Format | Content |
|-------|--------|---------|
| Content | `pool/events/<id>.md` | Event body text |
| Metadata | `events.jsonl` | `{id, t, access_count, fingerprint_idx}` |
| Fingerprints | `fingerprints.npy` | N × 1024 matrix (bge-m3) |
| Cosine | `cosine.npy` | N × N pairwise similarity |
| Edges | PyG `edge_index.npy` + `edge_attr.npy` | Typed directed edges (temporal/semantic/causal/remind/elaboration/contrast) |

### Beat sources

`cc` (dialogue) · `action` (tool use) · `tg` · `email` · `favilla` (mobile) · `schedule`

### Functional plugins

Optional integrations are registered by `plugins/<id>/plugin.toml`. Infrastructure such as dashboard, git diff, flow, Pool, and recall is not treated as a plugin. Inbound messages go through `fiam/receive/<source>`; outbound AI markers such as `[→tg:Iris] ...` are resolved through enabled plugin `dispatch_targets` and published to `fiam/dispatch/<target>`. See [docs/plugin_protocol.md](docs/plugin_protocol.md).

## Features

- **Manual-first annotation** — console marks event and drift cuts; processed flow ranges are locked in `store/annotation_state.json`
- **Frozen feature capture** — every ingested beat can be saved once into `store/features/flow_vectors.npy`
- **Real-time segmentation** — optional auto mode where Gorge watches beat embeddings and fires event cuts
- **Drift detection** — auto mode only: adjacent beat cosine below threshold → recall hook fires
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
  conductor.py          ★  # Beat ingestion → flow + frozen vectors; optional auto gorge/pool/recall
  plugins.py            ★  # plugin.toml registry + enable/disable helpers
  markers.py            ★  # generic [→target:recipient] marker parser
  gorge.py              ★  # TextTiling depth segmentation (batch + streaming)
  store/
    beat.py             ★  # Beat dataclass + flow.jsonl I/O
    pool.py             ★  # Pool 5-layer storage (content/meta/fingerprints/cosine/edges)
  retriever/
    spread.py           ★  # Graph spreading activation (seed→spread→select)
    embedder.py            # Multi-profile embedder (local/remote)
  adapter/
    claude_code.py         # CC JSONL → Turn/Beat parsing

scripts/
  fiam.py                  # CLI: init, start, stop, status, clean, find-sessions
  dashboard_server.py      # Web console backend (Pool + annotation API)
  fiam_lib/
    daemon.py              # Main event loop + CC session management
    maintenance.py         # clean + find-sessions
    postman.py             # TG/email protocol helpers
    scheduler.py           # Scheduled tasks (wake cycles)

dashboard/                 # SvelteKit 5 + Svelte runes + Tailwind 4
  src/routes/graph/        # 3D force-directed graph (Canvas 2D)
  src/routes/events/       # Event list + detail
  src/routes/flow/         # Beat stream viewer
  src/lib/                 # API client, NodeEditor, EdgeMenu

scripts/hooks/             # CC hook scripts
  inject.sh                # recall injection (UserPromptSubmit)
  outbox.sh                # outbound message extraction (Stop)
  boot.sh                  # daily summary (SessionStart)
  compact.sh               # archive summaries (PostCompact)

channels/
  tg/stickers/             # TG sticker index
  favilla/                 # Android text capture app
  limen/                   # ESP32 wearable device

plugins/                   # optional functional integration manifests
  tg/ email/ favilla/ xiao/ app/ voice-call/ device-control/ ring/ mcp/
```

## Commands

| Command | Description |
|---------|-------------|
| `fiam init` | Interactive setup — creates `fiam.toml` |
| `fiam start` | Start daemon (monitors sessions, subscribes MQTT ingress) |
| `fiam stop` | Graceful shutdown |
| `fiam status` | Show store counts + daemon state |
| `fiam clean` | Reset generated store data |
| `fiam find-sessions` | Debug Claude Code JSONL session paths |
| `fiam plugin list` | List functional plugin manifests |
| `fiam plugin show <id>` | Show one plugin's topics, capabilities, auth, and latency notes |
| `fiam plugin enable/disable <id>` | Toggle plugin receive/dispatch routing |

## Configuration

Copy `fiam.toml.example` → `fiam.toml` (or run `fiam init`).

Key settings:
- `embedding_backend`: `local` / `remote` — local HuggingFace or remote API
- `embedding_dim`: 1024 (bge-m3 default)
- `idle_timeout_minutes`: inactivity before post-session processing
- `tg_chat_id` / `email_*`: multi-channel settings
- `[conductor]` section: `memory_mode` (`manual` / `auto`), gorge window, confirm count, drift threshold
- `[graph]` section: DeepSeek-compatible edge model and API key env (`FIAM_GRAPH_API_KEY` by default)

## License

MIT
