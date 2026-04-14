# fiam — Fluid Injected Affective Memory

Long-term emotional memory for AI coding agents. Runs alongside Claude Code, watches conversation sessions, extracts emotionally significant events, builds a memory graph, and injects relevant memories back into future conversations — so the AI actually remembers.

## Features

- **Real-time recall** — daemon monitors CC sessions, detects topic drift via cosine similarity, and refreshes contextual memories on the fly
- **Emotion-aware storage** — WDI (Weighted Dimensional Intensity) classifier gates what gets stored; only emotionally resonant content becomes long-term memory
- **Memory graph** — events linked by semantic, temporal, causal, and associative edges; SYNAPSE-inspired graph diffusion for retrieval
- **Multi-channel communication** — Telegram and email inbound/outbound, with identity continuity across channels
- **Hook-mediated injection** — 4 CC hooks (UserPromptSubmit, Stop, SessionStart, PostCompact) for seamless context flow
- **Session management** — resume-based messaging, interactive lock, daily lifecycle with compact archival

## How It Works

```
Claude Code session
       │
       ├── JSONL log ──► fiam daemon ── topic drift? ──► refresh recall.md
       │                      │
       │                      ├── TG/email ──► inbox.jsonl ──► wake AI
       │                      └── outbox/*.md ──► dispatch via TG/email
       │
       └── Hooks ◄──── inject recall + inbox as additionalContext
                 ├──── extract [→tg:user] markers → outbox
                 ├──── inject daily summary on session start
                 └──── archive compact summaries
```

**Real-time loop**: daemon polls JSONL → embeds user text → cosine drift detection → retrieves memories → writes `recall.md` → hook injects every turn.

**Post-session**: idle timeout → emotion classification → salience gating → topic segmentation (TextTiling) → store events → build graph edges → refresh recall.

**Inbound**: daemon polls Telegram Bot API + IMAP → writes `inbox.jsonl` → hook injects on next turn (or wakes AI via `claude -p --resume`).

**Outbound**: AI writes `[→tg:user] message` in response → Stop hook extracts → `outbox/*.md` with YAML frontmatter → postman dispatches.

## Install

```bash
git clone https://github.com/Zephyr-Ovo/Fiam.git && cd Fiam
uv sync
uv run python scripts/fiam.py init    # interactive setup wizard
uv run python scripts/fiam.py start   # start daemon
cd ~/ai-home && claude                 # start CC from AI's home directory
```

Requires [uv](https://astral.sh/uv) and [Claude Code](https://claude.ai/code).

For remote embedding/emotion inference (recommended for low-RAM machines), deploy `serve_embeddings.py` on a GPU/high-RAM server and set `embedding_backend = "remote"` in `fiam.toml`.

## Structure

```
src/fiam/
  pipeline.py              # Pre/post session orchestration
  config.py                # FiamConfig dataclass + fiam.toml parsing
  adapter/                 # JSONL parsing (CC adapter, attachment handling)
  classifier/              # WDI emotion classification (local or remote)
  extractor/               # Salience gating + TextTiling segmentation
  retriever/               # Joint retrieval + SYNAPSE graph diffusion
  store/                   # EventRecord persistence + graph.jsonl edges
  synthesizer/             # Recall narrative generation
  prompts/                 # Editable prompt templates

scripts/
  fiam.py                  # CLI: init, start, stop, scan, status, graph, clean
  fiam_lib/
    daemon.py              # Main loop: poll, drift detection, session management
    postman.py             # TG/email dispatch + inbox polling
    recall.py              # recall.md writing from retrieved fragments
    awareness.py           # Environment map + situational context
    scheduler.py           # Scheduled tasks (wake cycles)

developer/hooks/           # CC hook scripts (deploy to ~/.claude/hooks/)
  inject.sh                # UserPromptSubmit: recall + inbox injection
  outbox.sh                # Stop: extract outbound message markers
  boot.sh                  # SessionStart: daily summary + interactive lock
  compact.sh               # PostCompact: archive compact summaries
```

## Commands

| Command | Description |
|---------|-------------|
| `fiam init` | Interactive setup wizard — creates `fiam.toml` |
| `fiam start` | Start daemon (monitors sessions, polls channels) |
| `fiam stop` | Graceful shutdown (processes pending content first) |
| `fiam scan` | One-time import of CC session history |
| `fiam status` | Show store counts + daemon state |
| `fiam graph` | Generate Obsidian-compatible graph visualization |
| `fiam clean` | Reset event store |

## Configuration

Copy `fiam.toml.example` → `fiam.toml` (or run `fiam init`).

Key settings:
- `language_profile`: `multi` (default) / `zh` / `en` — determines embedding + emotion models
- `emotion_provider`: `local` / `api` / `remote` — where emotion classification runs
- `embedding_backend`: `local` / `remote` — local HuggingFace or remote API server
- `arousal_threshold`: salience gate (default 0.6) — lower = more events stored
- `idle_timeout_minutes`: how long after last activity before processing (default 30)
- `tg_chat_id` / `email_*`: multi-channel communication settings

## License

MIT
