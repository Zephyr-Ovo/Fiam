# fiam — Fluid Injected Affective Memory

Long-term emotional memory for AI agents. Watches Claude Code sessions, extracts significant events, builds a memory graph, and injects relevant memories into future conversations.

## How It Works

```
Claude Code session
       │
       ├── JSONL log ──► fiam daemon ── topic drift? ──► refresh recall.md
       │                                                        │
       └── Hook (UserPromptSubmit) ◄──── inject (context) ◄────┘
```

**Real-time**: daemon monitors JSONL → embeds user text → cosine drift detection → retrieves memories → writes `recall.md`. Hook injects it every turn.

**Post-session**: idle timeout → emotion classification (WDI) → salience gating → topic segmentation (TextTiling) → store events → build graph edges → refresh recall.

## Install

```bash
git clone https://github.com/Zephyr-Ovo/Fiam.git && cd Fiam
uv sync
uv run python scripts/fiam.py init    # setup wizard
uv run python scripts/fiam.py start   # start daemon (separate terminal)
cd ~/your-home && claude               # start CC from home dir
```

Requires [uv](https://astral.sh/uv) and [Claude Code](https://claude.ai/code). Models download automatically on first run (~3 GB for multi-language profile).

## Structure

```
src/fiam/                  # Core pipeline
  pipeline.py              # Pre/post session orchestration
  config.py                # FiamConfig + fiam.toml
  classifier/              # WDI emotion (valence-arousal)
  extractor/               # Salience gating + topic segmentation
  retriever/               # Joint retrieval + graph diffusion (SYNAPSE)
  store/                   # EventRecord + graph.jsonl (edge store)
  synthesizer/             # Recall narrative + LLM synthesis
  prompts/                 # Editable .txt prompt templates
  adapter/                 # JSONL parsing (CC adapter protocol)
scripts/
  fiam.py                  # CLI entry point
  fiam_lib/                # Daemon, scheduler, awareness, comms
store/                     # Runtime (gitignored): events/, embeddings/, graph.jsonl
```

## Commands

```
fiam init / start / stop   Setup, daemon lifecycle
fiam scan                  Import CC history (one-time)
fiam status                Store counts + daemon state
fiam graph                 Obsidian graph visualization
fiam clean                 Reset store
fiam feedback              Interactive event rating
```

## Config

See `fiam.toml.example`. Key settings: `language_profile` (multi/zh/en), `emotion_provider` (local/api), `arousal_threshold`, `top_k`, `idle_timeout_minutes`. `[graph]` and `[narrative]` sections for LLM integration (DeepSeek).

## License

MIT
