# fiam

FIAM is a local-first memory and routing framework for AI agents. It records user, model, tool, browser, email, device, and app activity as structured events, keeps model-visible transcript history bounded, and moves slow memory work into durable background jobs.

The project is built around one rule: runtime adapters call models; they do not own persistence or side effects. Facts are committed through the turn pipeline, then read models and memory indexes are derived from those facts.

## Current Architecture

```text
Transport adapters
  HTTP / MQTT / Browser / Email / Device
        |
        v
TurnRequest / InboundQueue
        |
        v
Conductor.receive_turn()
        |
        v
PromptAssembler -> RuntimeAdapter -> MarkerInterpreter
        |
        v
TurnCommit
        |
        v
events.sqlite3 / ObjectStore / transcripts / UI history / state / todo / dispatch / trace
        |
        v
MemoryWorker / Dispatch bridges / dashboard read models
```

## Core Stores

- `events.sqlite3` is the source of truth for event facts.
- `store/objects/` is the content-addressed blob store for attachments, generated files, screenshots, and large tool results.
- `store/transcripts/{channel}.jsonl` is clean, bounded model-visible history.
- `home/transcript/{channel}.jsonl` is a UI read model.
- `store/turn_traces.jsonl` is observability only; it is not prompt or recall input.
- `store/timeline/*.md` is a MemoryWorker-derived timeline read model.
- `store/pool/*` is a derived memory index for graph recall and spreading activation.

Runtime data is ignored by git. Do not commit `store/`, `self/`, logs, local config, voice samples, or generated media.

## Main Concepts

- `TurnRequest` captures a normalized incoming turn with channel, surface, actor, text, attachments, ids, delivery policy, and trace metadata.
- `Conductor` owns the public turn boundary. Transport adapters should call `Conductor.receive_turn()` or commit a `TurnCommit`, not write facts directly.
- `PromptAssembler` builds API/plain prompts from constitution, system manual, self context, explicit recall context, selected timeline snippets, and clean transcript history.
- Runtime adapters are pure model callers. `ApiRuntime.ask()` returns structured results and transcript messages, but does not write events, state, todo, dispatch, or UI history.
- `MarkerInterpreter` is the single high-level XML marker parser for `<send>`, `<cot>`, `<hold>`, `<held>`, `<todo>`, `<wake>`, `<sleep>`, `<state>`, and `<route>`.
- `TurnCommit` is the single commit point for events, clean transcript messages, UI rows, state/todo read models, dispatch facts, and trace rows.
- `MemoryWorker` processes durable `memory_jobs`: event embedding/timeline, Pool graph edges, summary/object tags, transcript compaction, and recall warmup.

## Optional Surfaces

- `dashboard/` provides local web views for status, recent events, objects, trace, context, logs, and runtime controls.
- `channels/favilla/` is the companion app surface.
- `channels/atrium/` is the desktop/browser capture surface.
- `channels/obsidian-fiam-studio/` is an Obsidian Studio integration.
- `plugins/` contains optional receive/dispatch capability manifests.
- `scripts/bridges/` contains bridge implementations such as email dispatch.

These surfaces should stay optional. The core system should run without private device state, app build output, or local credentials.

## Markers And Plugins

AI-authored non-natural-language signals use XML markers inside model output. Examples:

```xml
<send to="email:zephyr@example.com" attach="obj:0123...abcd">Report attached.</send>
<todo at="2026-05-13 20:00">review traces</todo>
<state value="mute" reason="focus" />
<route family="gemini" reason="math fallback" />
```

See `docs/markers_protocol.md` for marker semantics and `docs/plugin_protocol.md` for plugin manifests.

## Install

Requirements:

- Python 3.11+
- `uv`
- Optional: Node.js for dashboard/app work
- Optional: MQTT broker for async device/plugin transport
- Optional: Claude Code for the Claude Code runtime adapter

```bash
git clone https://github.com/Zephyr-Ovo/Fiam.git
cd Fiam
uv sync
uv run python scripts/fiam.py init
uv run python scripts/fiam.py start
```

For local ML embeddings, install the optional ML dependencies if configured by your environment. Remote/OpenAI-compatible embeddings and model providers are configured through environment variable names, not committed secrets.

For the Claude Code channel transport used in deployment, install the MCP channel helper once:

```bash
npm --prefix channels/cc-channel install
FIAM_CC_TRANSPORT=channel uv run python scripts/fiam.py start
```

This runs each automated Claude Code turn through an official one-way MCP channel and reconstructs replies from the Claude Code JSONL transcript. Without `FIAM_CC_TRANSPORT=channel`, Fiam keeps the legacy `claude -p` path for local development.

## Configuration

Copy `fiam.toml.example` to `fiam.toml`, or run:

```bash
uv run python scripts/fiam.py init
```

Important sections:

- `[api]`: default OpenAI-compatible provider/model for API runtime.
- `[catalog.<family>]`: explicit route/model family configuration.
- `[app]`: app runtime defaults and COT summary config.
- `[conductor]`: memory mode, gorge/drift/recall settings.
- `[mqtt]`: MQTT transport settings.
- `[graph]`: optional edge typing/event naming model.
- `[vision]`: optional image description model.
- `[voice.stt]` / `[voice.tts]`: optional voice providers.

Secrets must live in environment variables named by config, for example `POE_API_KEY`, `GEMINI_API_KEY`, `FIAM_GRAPH_API_KEY`, `FIAM_SUMMARY_API_KEY`, or deployment-specific equivalents. Do not commit real keys or local config.

## CLI

```bash
uv run python scripts/fiam.py init
uv run python scripts/fiam.py start
uv run python scripts/fiam.py stop
uv run python scripts/fiam.py status
uv run python scripts/fiam.py clean
uv run python scripts/fiam.py plugin list
uv run python scripts/fiam.py api --channel chat "ping"
```

## Local Reset

To inspect what a local Favilla/FIAM whiteboard reset would clear:

```bash
python scripts/reset_favilla_whiteboard.py
```

Apply it with:

```bash
python scripts/reset_favilla_whiteboard.py --apply
```

The reset truncates AI prompt markdown placeholders under the configured home, clears local UI/model transcripts, cuts/session state, derived memory/training stores, ObjectStore blobs, timeline/features/pool data, and leaves source files, README/docs, config, secrets, and git history untouched.

Claude Code can return a generated home file to Favilla as a downloadable object with:

```bash
python scripts/object_put.py --path relative-file.txt --direction outbound
```

`fiam api` is a pure runtime smoke call. It does not write events or transcripts.

## Development

Run focused Python tests:

```bash
python -m unittest tests.test_turn_pipeline tests.test_api_runtime
```

Run broad discovery while excluding known environment-specific tests only when needed:

```bash
python -m unittest discover tests
```

Frontend checks depend on the surface:

```bash
npm --prefix dashboard run check
npm --prefix channels/favilla/app run build
```

## Open Source Hygiene

The repository intentionally ignores:

- `fiam.toml`, `.env*`, `*.secret`, key files, and local device secrets
- `store/`, `self/`, logs, trace dumps, generated SQLite/JSONL runtime state
- `.venv/`, `node_modules/`, build output, app bundles, generated browser extensions
- local voice/STT experiments and generated media under `stt/`, `tts/`, `mimo_api_assets/`
- `btw/`, `archive/`, `pic/`, and other personal scratch or asset folders

Before publishing, review `git status --ignored` and keep only source, tests, protocol docs, templates, and safe examples.

## License

MIT
