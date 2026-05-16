# Fiam

Fiam is an AI agent architecture. Developer: Zephyr (iris.zhou43@outlook.com).

## Project Structure

- `src/fiam/` — Core library (config, runtime, channels, turn processing)
  - `runtime/api.py` — API client layer (OpenAI-compatible, Anthropic, Vertex, fallback)
  - `runtime/prompt.py` — Prompt assembly, transcript management
  - `runtime/tools.py` — Tool schemas and execution
  - `runtime/turns.py` — Turn management
- `scripts/` — Server processes
  - `dashboard_server.py` — Main HTTP/WS server (Favilla API, dashboard, SSE)
  - `fiam.py` — Daemon entry point
  - `fiam_lib/` — Daemon support (hooks, maintenance, CC channel, postman)
- `channels/favilla/` — Mobile app (React + Capacitor)
  - `app/src/lib/api.ts` — API client
  - `app/src/lib/voice.ts` — STT/TTS providers
  - `app/src/App.tsx` — Main chat UI
- `store/` — Persistent data (transcripts, objects)

## Key Paths

- Agent home: `/home/live` (runtime state, self/, transcript/, uploads/)
- Codebase: `/home/fiet/fiam-code`
- Agent conversational CLAUDE.md: `/home/live/CLAUDE.md` (not this file)
- Config: `fiam.toml`
- Env: `.env` (secrets, API keys)
- Start scripts: `/home/fiet/start_dashboard.sh`

## Services

- Dashboard: `scripts/dashboard_server.py --port 8766` (needs `.env` sourced)
- Fiam daemon: `scripts/fiam.py start` (needs `.env` sourced)
- Restart dashboard: `bash /home/fiet/start_dashboard.sh`

## Known Issues / Patterns

- Tool use 400 errors: Anthropic rejects `content: null` in assistant messages.
  Fixed in `api.py` and `prompt.py` by normalizing null to `""`.
  If similar 400s recur, check message format sent to provider.
- API provider: Poe (OpenAI-compatible) → Anthropic backend. Format mismatches
  between OpenAI and Anthropic specs are a recurring source of bugs.
- Transcript is stored in two places: `/home/live/transcript/` (app-facing)
  and `store/transcripts/` (API-facing). They serve different purposes.

## Deploying Favilla (Android)

The app is built via GitHub Actions, not locally (server has no Java/Android SDK).

1. Commit and push to `main` (changes under `channels/favilla/app/` trigger the workflow)
2. CI runs `.github/workflows/favilla-android.yml`: npm ci → vite build → cap sync → gradle assembleDebug
3. APK artifact uploaded as `favilla-debug-apk`
4. Download artifact: `gh run download <run-id> -n favilla-debug-apk`
5. Install via adb (phone connected via reverse tunnel): `adb install -r -d app-debug.apk`

Windows shortcut: `pwsh scripts/deploy_favilla.ps1` (push → poll CI → download → adb install)

## Restarting Services

```bash
# Dashboard (sources .env automatically)
bash /home/fiet/start_dashboard.sh

# Fiam daemon
pkill -f "fiam.py start"
env $(cat .env | grep -v '^#' | xargs) nohup .venv/bin/python scripts/fiam.py start > /tmp/fiam-start.log 2>&1 &
```

## Dev Notes

- Always source `.env` before starting services.
- After code changes in `src/fiam/`, restart both dashboard and fiam daemon.
- After frontend changes in `channels/favilla/app/`, run `npm run build` then deploy via CI.
- Mimo models are all reasoning models. Use `mimo-v2-omni` for lightweight tasks
  (COT summary, translate, recall narration) — it has shorter reasoning chains.
  Keep `max_tokens` generous (800+) to accommodate reasoning overhead.
- `manual.md` lives at `/home/live/manual.md` (deployed from `scripts/templates/manual.md`).
  Update both when changing XML markers or agent instructions.
