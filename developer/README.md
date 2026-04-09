# fiam — Developer Toolbox

Common commands for development, testing, and maintenance.

---

## Setup

After `uv sync`, the `fiam` binary is installed in `.venv`. Activate the venv once to use
`fiam` directly without the `uv run` prefix:

**Windows (PowerShell)**
```powershell
.venv\Scripts\Activate.ps1
fiam --help
```

**Linux / macOS**
```bash
source .venv/bin/activate
fiam --help
```

For permanent PATH setup and GPU/API mode configuration, see [developer/beta.md](beta.md).

---

## Emotion Modes

Two modes, same commands — only `fiam.toml` differs:

| Mode | `fiam.toml` | RAM | First-run download | Best for |
|---|---|---|---|---|
| **local** (default) | `emotion_provider = "local"` | 4–6 GB | ~2.2 GB models | GPU users, offline |
| **api** | `emotion_provider = "api"` | ~1 GB | ~1.5 GB (embedder only) | Servers, no GPU |

API mode uses the same Anthropic key as Claude Code. Cost: ~$0.01/session (haiku).

---

## Commands

| Command | Purpose |
|---|---|
| `fiam init` | Interactive setup wizard (writes fiam.toml, hook files) |
| `fiam start` | Start daemon — polls JSONL, processes on idle timeout |
| `fiam stop` | Graceful stop — processes pending content, then exits |
| `fiam status` | Daemon status + event/embedding counts |
| `fiam scan` | One-time full history import (run after `fiam init` on existing installs) |
| `fiam clean` | Reset store to factory-fresh state (events, embeddings, logs, cursor) |
| `fiam reindex` | Rebuild all embeddings with current model (after model change) |
| `fiam graph` | Generate Obsidian wikilink graph from event store |
| `fiam add-home <path>` | Add a home directory (sets up CLAUDE.md, hooks, structure) |
| `fiam remove-home <path>` | Remove a home from config (data NOT deleted) |
| `fiam pre` | Run pre_session once manually |
| `fiam post` | Run post_session once manually |
| `fiam find-sessions` | List all JSONL files Claude Code has written for a home path |

All commands that touch the store accept `--home` and `--debug` flags.

---

## Post-Session Workflows

`fiam post` runs the post_session pipeline manually: parse JSONL → classify emotions → 
apply significance gates → write events → refresh recall.md. Normally triggered automatically 
by the daemon after 30+ minutes of idle time, but you can invoke it at any time.

### Basic post-session

Process the latest JSONL session from your home:

```powershell
# Use fiam.toml config (if home_path is set)
fiam post

# Specify home explicitly
fiam post --home <your-home>
```

Produces a timestamped session folder in `logs/sessions/` with extraction traces, 
reports, and event writes.

### Test mode: fixture files

Process a known test conversation without needing a real Claude Code session:

```powershell
# Run post_session on a test fixture
fiam post --test-file test_vault/fixtures/emotional_gradient.json

# With debug output
fiam post --test-file test_vault/fixtures/session377.json --debug
```

Fixture format: JSON array of `{"role": "user"|"assistant", "text": "..."}` objects.

See `test_vault/fixtures/` for example conversations designed to test significance gates, 
emotion detection, and cross-language retrieval.

### Tuning thresholds during development

Post-session uses arousal_threshold, novelty_threshold, etc. from fiam.toml. To quickly 
test different gate values:

```powershell
# Temporarily lower arousal gate to capture more events
# Edit fiam.toml: arousal_threshold = 0.3 (default 0.6)
fiam post --test-file test_vault/fixtures/emotional_gradient.json --debug

# Check logs/sessions/<latest>/extractor.json for gate decisions
```

### Verbose inspection

```powershell
# Full debug trace: similarity scores, gate decisions, merged events
fiam post --debug

# Typical output includes:
#   Event 1: arousal=0.82, novelty=0.45 → emotional gate PASS
#   Event 2: arousal=0.35, novelty=0.92 → novelty gate PASS (arousal failed)
#   Merged 2 events into 1 (duplicate cluster)  → recall refreshed
```

### Daemon vs. manual

| Trigger | Auto-idle | Manual | Use case |
|---|---|---|---|
| **When** | 30+ min no activity | Any time | Daily iteration, testing, CI/CD |
| **Coverage** | Latest JSONL only | Configurable | Existing session processing |
| **Gating** | Uses live thresholds | Live thresholds | Threshold tuning |
| **Output** | `store/`, `recall.md` | `store/`, `recall.md` + logs | Debugging |

---

## Testing with a fixture file

```powershell
# Run post_session on a known conversation fixture
fiam post --test-file test_vault/fixtures/session377.json
```

Fixture format: JSON array of `{"role": "user"|"assistant", "text": "..."}` objects.

---

## Debug mode

Enable verbose logging for any pipeline stage:

```powershell
fiam start   --debug    # daemon: every poll cycle, retrieval scores, topic drift checks
fiam pre     --debug    # pre_session: embedding + retrieval traces
fiam post    --debug    # post_session: extraction gates, event merging, vault writes
fiam reindex --debug    # reindex: per-event embedding + metadata updates
```

Debug output includes:
- **Similarity scores**: cosine distance between query and all events  
- **Gate decisions**: why events passed/failed arousal, novelty, elaboration thresholds
- **Merging trace**: which events were clustered and why  
- **Vault writes**: exact YAML + metadata written to each `.md` file
- **Recall refresh**: before/after content injected into recall.md

Logs are written to `logs/sessions/<timestamp>/` for each post_session run, or to stderr 
for daemon cycles.

---

## Reindexing after a model change

If you change `embedding_model` or `language_profile` in fiam.toml, old
embeddings may be dimensionally incompatible. Rebuild everything:

```powershell
fiam reindex
```

This rewrites all `.npy` files and updates metadata in every event `.md`.

---

## Inspecting JSONL session files

```powershell
# Find which JSONL directory matches your home
fiam find-sessions --home <your-home>

# Read a session manually (CC slugifies the path: slashes → hyphens, colon → hyphen)
# e.g. D:\ai-home → D--ai-home
Get-Content "$env:USERPROFILE\.claude\projects\<home-slug>\<session>.jsonl"
```

---

## Resetting between test runs

```powershell
fiam clean        # prompts for confirmation
fiam clean --yes  # skip confirmation (CI / scripting)
```

`fiam clean` wipes: events, embeddings, graph, cursor, session logs, recall.md.
It does **not** touch fiam.toml or hook files.

---

## Session log layout

Each `fiam post` run (or daemon cycle) produces a session folder:

```
logs/sessions/
  MMDD_HHMM/
    conversation.txt   — raw parsed turns
    extractor.json     — event extraction debug output
    value_shift.json   — value shift analysis
    value_trigger.json — trigger detection
    report.json        — full pipeline report
    report.md          — human-readable summary
    vault_write.json   — store write confirmation
```

---

## Troubleshooting Common Issues

### No events saved after `fiam post`

Check the significance gates in `logs/sessions/<latest>/extractor.json`:

```powershell
# Events show arousal < 0.6 and novelty < 0.7?
# They failed the gates. Either:
#   1. The conversation lacks emotional signal (try a different test fixture)
#   2. Gates are too high for your use case (lower arousal_threshold in fiam.toml)
#   3. Events are duplicates (novelty gate filtering similar past memories)
```

Lower thresholds for testing:  
```powershell
# fiam.toml
arousal_threshold = 0.3    # default 0.6 — capture more every-day moments
novelty_threshold = 0.5    # default 0.7 — allow similar events
```

Then re-run: `fiam post --test-file ... --debug`

### Daemon not processing sessions

```powershell
# Check daemon is running
fiam status

# If "not running", start it
fiam start

# Check JSONL exists
fiam find-sessions --home <your-home>

# If no sessions found:
#   - Start Claude Code from your home directory
#   - Have a conversation (fiam only sees JSONL changes, not idle windows)
#   - Wait for daemon to detect (~30 sec polling interval)
```

### Embedding dimension mismatch

Happens after switching `language_profile` or `embedding_model`. Old `.npy` files don't 
match new model dimensions.

```powershell
# Rebuild embeddings for existing events
fiam reindex

# If you only want to restart with fresh events
fiam clean     # wipes store
fiam scan      # re-processes JSONL
```

### Hook not injecting recall into Claude Code

```powershell
# Verify hook is installed
Test-Path <your-home>\.claude\hooks\inject.ps1

# Test the hook directly
$env:CLAUDE_PROJECT_DIR = "<your-home>"
& "<your-home>\.claude\hooks\inject.ps1"

# Should output JSON like:
#   {"hookSpecificOutput":{"additionalContext":"- (3d ago) ..."}}

# If no output:
#   - Check <your-home>\recall.md exists and has content
#   - Check <your-home>\.claude\settings.local.json has UserPromptSubmit hook configured
```

In Claude Code, run: `/hooks` → should see "1 hook configured" under UserPromptSubmit.

---

## Performance Profiling

### Event extraction bottleneck

Most time spent in emotion classification (transformers inference). Profile with:

```powershell
fiam post --debug 2>&1 | Select-String "emotion|analysis time"

# If emotion classification is slow:
#   - Use a smaller or quantized model
#   - Use GPU (check torch.cuda.is_available() in emotion.py)
#   - Batch multiple texts if possible
```

### Retrieval latency

Pre-session (live recall injection) must be fast (<1s). Check:

```powershell
# logs/sessions/*/report.md should show:
#   pre_session: retrieval latency

# If > 1s:
#   - Event store is large (1000+ events)
#   - Consider increasing similarity_threshold or reducing top_k
```

---

## CI/CD Integration

Example: test every fixture in automation:

```powershell
foreach ($fixture in Get-ChildItem test_vault/fixtures/*.json) {
    Write-Host "Testing $($fixture.Name)..."
    uv run python scripts/fiam.py post --test-file $fixture.FullName --debug
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed: $($fixture.Name)"
        exit 1
    }
}
Write-Host "All fixtures passed."
```

For strict validation:

```powershell
fiam clean --yes
fiam scan --home <test-home>        # import historical JSONL
fiam post --debug                   # process once
if (!(Test-Path store/events/*.md)) {
    Write-Error "No events extracted!"
    exit 1
}
```
