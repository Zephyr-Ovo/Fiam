# developer/

Developer notes, design docs, test plans.

---

## Quick setup

```bash
uv sync
source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows
fiam --help
```

See [beta.md](beta.md) for GPU / API mode config.

---

## CLI reference

All commands accept `--home <path>` and `--debug`.

| Command | Purpose |
|---|---|
| `fiam init` | Setup wizard — writes fiam.toml + hook files |
| `fiam start` | Start daemon |
| `fiam stop` | Graceful stop (processes pending then exits) |
| `fiam status` | Daemon status + store counts |
| `fiam scan` | Full history import (run once after init on existing install) |
| `fiam pre` | Run pre_session manually |
| `fiam post` | Run post_session manually |
| `fiam post --test-file <path>` | Run post_session against a fixture JSON |
| `fiam find-sessions` | List JSONL files for a home path |
| `fiam reindex` | Rebuild all embeddings (after model change) |
| `fiam clean [--yes]` | Wipe store: events, embeddings, graph, cursor, logs |
| `fiam graph` | Generate Obsidian wikilink graph (local only) |

---

## Testing

Fixture format: `[{"role": "user"|"assistant", "text": "..."}]`

```bash
fiam post --test-file test_vault/fixtures/emotional_gradient.json --debug
```

Debug output: gate decisions (`intensity`, `novelty`, `elaboration`), cosine scores, merge trace.
Logs written to `logs/sessions/<MMDD_HHMM>/`.

Threshold tuning (fiam.toml):
```toml
novelty_threshold = 0.5   # default 0.7 — lower to allow similar events
```

---

## Common issues

**No events saved** — check `logs/sessions/<latest>/extractor.json` for gate decisions.
Most often: arousal too low, or events too similar to existing store (novelty gate).

**Embedding mismatch after model change** — `fiam reindex`.

**Hook not injecting** — test directly:
```powershell
$env:CLAUDE_PROJECT_DIR = "<your-home>"
& "<your-home>\.claude\hooks\inject.ps1"
# should print {"hookSpecificOutput": {"additionalContext": "..."}}
```

**JSONL not found** — `fiam find-sessions`. If empty, start Claude Code from the home dir and have a conversation first.

---

## Session log layout

```
logs/sessions/MMDD_HHMM/
  conversation.txt
  extractor.json
  report.json / report.md
  vault_write.json
```
