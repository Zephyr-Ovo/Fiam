# fiam — Fluid Injected Affective Memory

Long-term emotional memory for AI coding agents. Runs alongside Claude Code, watches conversation sessions, extracts emotionally significant events, builds a memory graph, and injects relevant memories back into future conversations — so the AI actually remembers.

## Features

- **Real-time recall** — daemon monitors CC sessions, detects topic drift via cosine similarity, and refreshes contextual memories on the fly
- **Intensity-aware storage** — text intensity heuristic scores conversational heat; TextTiling depth segmentation decides structure, not emotion gates
- **Memory graph** — events linked by semantic, temporal, causal, and associative edges; SYNAPSE-inspired spreading activation with fire-once propagation and fan penalty
- **Multi-channel communication** — Telegram and email inbound/outbound, with identity continuity across channels
- **Affective state** — Goals→Appraisal→State pipeline: reads `goals.md` + recent events → LLM appraises emotional impact → writes `state.md` (mood, tension, reflection) → injected into synthesis
- **Memory replay** — during idle periods the daemon re-activates fading-but-important memories (`intensity × (1 − retention)` priority), mimicking hippocampal consolidation
- **Self-profile materials** — `fiam self-profile` distills memory graph into `self/materials.md` (centrality, intensity peaks, active hours, goal history) that the AI reads to self-author `personality.md` / `interests.md`
- **Trajectory logging** — every post-session transition recorded as JSONL with state-before/action/reward-signals/state-after, ready for future offline RL fine-tuning
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

**Post-session**: idle timeout → text intensity scoring → TextTiling depth segmentation → store events → build graph edges (DS open type system) → refresh recall.

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

For remote embedding inference (recommended for low-RAM machines), deploy `serve_embeddings.py` on a GPU/high-RAM server and set `embedding_backend = "remote"` in `fiam.toml`.

## Structure

```
src/fiam/
  pipeline.py              # Pre/post session orchestration
  config.py                # FiamConfig dataclass + fiam.toml parsing
  adapter/                 # JSONL parsing (CC adapter, attachment handling)
  classifier/              # Text intensity heuristic (surface-level conversational heat)
  extractor/               # TextTiling depth segmentation
  retriever/               # Joint retrieval + SYNAPSE spreading activation (fire-once, fan penalty)
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
| `fiam self-profile` | Regenerate `self/materials.md` from the memory graph |
| `fiam clean` | Reset event store |

## Configuration

Copy `fiam.toml.example` → `fiam.toml` (or run `fiam init`).

Key settings:
- `language_profile`: `multi` (default) / `zh` / `en` — determines embedding model
- `embedding_backend`: `local` / `remote` — local HuggingFace or remote API server
- `idle_timeout_minutes`: how long after last activity before processing (default 30)
- `tg_chat_id` / `email_*`: multi-channel communication settings

## License

MIT
