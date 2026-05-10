# Runtime Interaction Trace — 2026-05-07

This note records the actual operations run while checking ISP capacity, remote embedding, and Vertex Gemini API routing. It intentionally lists env names and file paths, not secret values.

## Server State

- Before cleanup: `/dev/vda1` was `25G` total, `25G` used, `117M` free, `100%`.
- Main removable usage found:
  - `/root/.cache/uv` about `6.8G`
  - `/root/.cache/pip` about `3.2G`
  - `/home/fiet/.cache/pip` about `2.9G`
  - `/home/fiet/.cache/uv` about `749M`
  - `/home/fiet/.cache/huggingface` about `857M`
- Cleaned with approval: root/user pip caches, root/user uv caches, and old user HuggingFace cache.
- After cleanup: `/dev/vda1` is `25G` total, `12G` used, `14G` free, `46%`.

## Embedding Check

Server config:

```toml
[models]
embedding = "BAAI/bge-m3"
embedding_backend = "remote"
embedding_remote_url = "http://127.0.0.1:8819"
embedding_dim = 1024
```

Observed listener:

```text
/usr/bin/ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -L 8819:127.0.0.1:8819 do
```

Health result:

```json
{"status":"ok","model":"BAAI/bge-m3"}
```

Real embed smoke:

```text
remote http://127.0.0.1:8819 BAAI/bge-m3 1024
1024 -0.055926
```

Conclusion: BGE-M3 is alive through the remote tunnel. ISP is not running the semantic model locally.

## Vertex Configuration

Server env names now used:

```text
GOOGLE_APPLICATION_CREDENTIALS=/home/fiet/.fiam/project-afda332a-1d3d-4334-863-cdf729adc69f.json
GOOGLE_CLOUD_PROJECT=project-afda332a-1d3d-4334-863
GOOGLE_CLOUD_LOCATION=us-central1
```

Server `fiam.toml` API route now prefers the Google Gemini API key quota and falls back to Vertex:

```toml
[api]
provider = "google_openai"
model = "gemini-2.5-flash"
base_url = ""
api_key_env = "GEMINI_API_KEY"
temperature = 0.7
max_tokens = 2048
timeout_seconds = 60
tools_enabled = true
tools_max_loops = 10

[api.fallback]
provider = "vertex_openai"
model = "google/gemini-2.5-flash"
base_url = ""
api_key_env = "GOOGLE_APPLICATION_CREDENTIALS"

[vision]
provider = "vertex_openai"
model = "google/gemini-2.5-flash"
base_url = ""
api_key_env = "GOOGLE_APPLICATION_CREDENTIALS"
```

Dependencies added/installed: `google-auth>=2.35.0`, `requests>=2.32.0`.

Google API key env names set locally and on ISP: `GEMINI_API_KEY` and `GOOGLE_API_KEY`. Do not print the value.

## Actual API Smoke

Raw Vertex request body:

```json
{
  "model": "google/gemini-2.5-flash",
  "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
  "temperature": 0,
  "max_tokens": 32
}
```

Raw response summary:

```json
{
  "model": "google/gemini-2.5-flash",
  "content": "pong",
  "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 32}
}
```

Note: with `max_tokens=16`, Gemini 2.5 consumed hidden reasoning tokens and returned an empty content field. The endpoint was healthy; the smoke cap was too small.

## Actual Favilla API Smoke

After the fallback small fix, the live Favilla API smoke used the Google Gemini API key primary route:

```json
{
  "ok": true,
  "runtime": "api",
  "reply": "pong",
  "model": "gemini-2.5-flash",
  "usage": {"prompt_tokens": 1501, "completion_tokens": 1, "total_tokens": 1525}
}
```

The earlier Vertex-only smoke also passed and is retained below as proof that fallback credentials and endpoint are healthy.

Trigger operation:

```http
POST http://127.0.0.1:8766/favilla/chat/send
X-Fiam-Token: <server env FIAM_INGEST_TOKEN>
Content-Type: application/json
```

Payload:

```json
{
  "text": "runtime=api 请只回复 pong，用于 Vertex smoke。",
  "runtime": "api",
  "source": "chat"
}
```

Observed response:

```json
{
  "ok": true,
  "runtime": "api",
  "reply": "pong",
  "model": "google/gemini-2.5-flash",
  "usage": {"prompt_tokens": 3937, "completion_tokens": 1, "total_tokens": 3989},
  "recall_fragments": 2,
  "dispatched": 0
}
```

Route chain:

```text
/favilla/chat/send
→ _favilla_chat_send(payload)
→ _run_api_favilla_chat(text=..., source="chat", attachments=[])
→ ApiRuntime.ask(..., extra_context=_app_runtime_context())
→ build_api_messages(config, clean, source="chat", include_recall=True, consume_recall_dirty=True, extra_context=...)
→ Vertex OpenAI-compatible /chat/completions
```

The model-facing user text included this runtime tag:

```text
[favilla:chat runtime=api] runtime=api 请只回复 pong，用于 Vertex smoke。
```

The full generated message list also includes local identity, recall, and app runtime context. It is intentionally not pasted into this repo doc because it may contain private self/recall text. To audit the exact full prompt on the server, run a controlled dump to a private file under `/home/fiet/fiet-home/debug/` before the request.

## Actual Image Smoke

First operation:

```http
POST http://127.0.0.1:8766/favilla/upload
```

Payload shape:

```json
{
  "files": [{"name": "vertex-red-smoke.png", "mime": "image/png", "data": "<base64 1x1 red png>"}]
}
```

Upload result summary:

```json
{"name":"vertex-red-smoke.png","mime":"image/png","size":69}
```

Second operation:

```http
POST http://127.0.0.1:8766/favilla/chat/send
```

Payload:

```json
{
  "text": "runtime=api 这张测试图主要是什么颜色？只回答一个中文颜色词。",
  "runtime": "api",
  "source": "chat",
  "attachments": [{"name":"vertex-red-smoke.png","mime":"image/png","size":69,"path":"/home/fiet/fiet-home/uploads/2026-05-07/b1ff9c8ea3a7-vertex-red-smoke.png"}]
}
```

Observed response summary:

```json
{
  "ok": true,
  "runtime": "api",
  "model": "google/gemini-2.5-flash-lite",
  "reply": "红"
}
```

This validates the direct multimodal path when the selected model can read images. The code fallback now does this instead for text-only main models:

```text
image attachments
→ configured [vision] Vertex Gemini describes image as text
→ image description is appended to the final user message
→ main text model continues with tools enabled
```

## Cleanup Performed

Removed the smoke artifacts from:

- `/home/fiet/fiet-home/app_history/chat.jsonl`
- `/home/fiet/fiam-code/store/flow.jsonl`
- `/home/fiet/fiet-home/uploads/2026-05-07/b1ff9c8ea3a7-vertex-red-smoke.png`
- `/home/fiet/fiet-home/uploads/manifest.jsonl`

Final visible status:

```json
{
  "daemon": "running",
  "events": 2,
  "embeddings": 58,
  "flow_beats": 45,
  "thinking_beats": 1,
  "interaction_beats": 0
}
```

## Console Auth Fix

The public dashboard used Caddy Basic Auth for `/`, but the user only remembered the old token-login flow. Caddy was changed to reverse-proxy all paths to `dashboard_server.py`; backend viewer auth now handles:

```text
/login?token=<FIAM_VIEW_TOKEN> -> Set-Cookie: fiam_view=<token>; Location: /
```

`FIAM_VIEW_TOKEN` is currently set from the existing `FIAM_INGEST_TOKEN` in server `.env`. Verification:

```text
GET / without token -> 401
GET /login?token=<view token> -> 302 /
GET /favilla/* OPTIONS -> 204
```

Longer-term recommendation: put Cloudflare Access / Zero Trust in front of `fiet.cc` and use passkeys/social login. That gives Windows Hello / Face ID / Touch ID without writing custom biometric auth logic in Fiam.

## CC Ping Trace

Full artifacts copied locally:

- [docs/cc_trace_2026-05-07_ping_prompt.md](cc_trace_2026-05-07_ping_prompt.md)
- [docs/cc_trace_2026-05-07_ping_response.json](cc_trace_2026-05-07_ping_response.json)
- [docs/cc_trace_2026-05-07_natural_console_response.json](cc_trace_2026-05-07_natural_console_response.json)
- [docs/cc_trace_2026-05-07_raw_console_response.json](cc_trace_2026-05-07_raw_console_response.json)
- [docs/cc_trace_2026-05-07_real_cc_selfcheck_response.json](cc_trace_2026-05-07_real_cc_selfcheck_response.json)
- [docs/cc_trace_2026-05-07_action_probe_final_response.json](cc_trace_2026-05-07_action_probe_final_response.json)
- [docs/cc_trace_2026-05-07_blind_probe_response.json](cc_trace_2026-05-07_blind_probe_response.json)
- [docs/cc_trace_2026-05-07_blind_probe_source_response.json](cc_trace_2026-05-07_blind_probe_source_response.json)

Initial result: `runtime=cc` failed with a 500 because Claude Code inside `fiam-dashboard.service` returned `401 Invalid authentication credentials`. Direct SSH Claude tests succeeded, and the exact same prompt/CLI args succeeded outside systemd. Root cause was the systemd sandbox: `ProtectHome=read-only` did not give Claude Code enough writable access to its account/cache directories.

Fix applied to `deploy/fiam-dashboard.service` and deployed to `/etc/systemd/system/fiam-dashboard.service`:

```text
ReadWritePaths=/home/fiet/fiet-home /home/fiet/fiam-code/logs /home/fiet/fiam-code/store /home/fiet/.claude /home/fiet/.cache /home/fiet/.config /tmp
ProtectHome=read-only
```

Trigger operation:

```http
POST http://127.0.0.1:8766/favilla/chat/send
X-Fiam-Token: <server env FIAM_INGEST_TOKEN>
Content-Type: application/json
```

Payload:

```json
{
  "text": "runtime=cc 请只回复 CC_PONG，不要使用工具。这是 console 基础连通性测试。",
  "runtime": "cc",
  "source": "chat"
}
```

Route chain:

```text
/favilla/chat/send
→ _favilla_chat_send(payload)
→ _run_cc_favilla_chat(text=..., source="chat", attachments=[])
→ build_plain_prompt_parts(..., extra_context=_app_runtime_context())
→ claude -p <user_prompt> --append-system-prompt <system_context> --output-format json ...
→ _apply_app_control_markers + _parse_cot
→ _record_cc_app_turn
→ app_history user/ai rows
```

Actual CLI shape:

```text
claude -p <user_prompt> --output-format json --max-turns 10 --setting-sources user --exclude-dynamic-system-prompt-sections --permission-mode bypassPermissions --append-system-prompt <system_context>
```

Actual model-facing user prompt for this ping:

```text
[wake:chat] [recall]
system reminders

[wake:chat] [favilla:chat runtime=cc] runtime=cc 请只回复 CC_PONG，不要使用工具。这是 console 基础连通性测试。
```

Observed response:

```json
{
  "ok": true,
  "runtime": "cc",
  "reply": "CC_PONG",
  "session_id": "bcb34917-1ea4-49e8-991f-0294c0d6f127",
  "subtype": "success",
  "cost_usd": 0.06361649999999999,
  "segments": [{"type": "text", "text": "CC_PONG"}],
  "queued_todos": 0,
  "queued_holds": 0,
  "carry_over": null
}
```

Writes observed before cleanup:

```text
/home/fiet/fiet-home/app_history/chat.jsonl: user + ai rows
/home/fiet/fiam-code/store/flow.jsonl: user + assistant CC beats
```

Cleanup performed:

```text
Removed 2 app_history rows and 4 flow rows matching the CC smoke patterns.
Removed the smoke active CC session if present.
```

### Console-Visible Natural Test

The first visible test used a synthetic `CC_VISIBLE_*` label. User feedback: tests shown in console should read like real conversation, with Fiet naturally explaining who it is and what is happening. The synthetic row was removed.

A replacement prompt then asked Fiet to naturally describe the current validation. During that attempt, Fiet included a literal outbound marker example inside Markdown inline code. Existing `parse_outbound_markers` still parsed that code sample as a real marker, which caused `assistant_text_beats` to create a bogus `source=dispatch` flow row. No actual email dispatch was intended; the visible symptom was a split flow entry.

Fix applied and deployed:

```text
src/fiam/markers.py: parse_outbound_markers now masks Markdown fenced code blocks and inline code spans before matching outbound markers.
tests/test_markers.py: added regression coverage for literal marker examples inside Markdown code.
Local test: f:/fiam-code/.venv/Scripts/python.exe -m pytest tests/test_markers.py -> 11 passed.
Server syntax check: .venv/bin/python -m py_compile src/fiam/markers.py.
Services restarted: fiam-dashboard.service and fiam-daemon.service active.
```

Final retained visible prompt:

```json
{
  "text": "runtime=cc Zephyr 正在看 console。当前事实：页面调用的是 /favilla/chat/send，runtime=cc；我们刚修好 token 登录、Claude Code 在 dashboard 服务里的鉴权问题，以及代码片段里的外发标记误触发问题。Fiet，请自然回复她：你是谁、这轮正在确认什么、她在页面上看到哪两个信号就算成功。",
  "runtime": "cc",
  "source": "chat"
}
```

Final retained response summary:

```json
{
  "ok": true,
  "runtime": "cc",
  "reply": "嘿，我是 Fiet，这轮走的是 Claude Code 通路...都看到的话，这轮就算过了。",
  "session_id": "ce8f1d4f-21cd-4948-a01c-78d6acfab437",
  "subtype": "success",
  "cost_usd": 0.07188525,
  "thoughts_locked": false,
  "segments": ["thought", "text"]
}
```

Retained temporarily for user observation:

```text
/home/fiet/fiet-home/app_history/chat.jsonl: final natural user + ai rows
/home/fiet/fiam-code/store/flow.jsonl: final natural user + assistant CC rows
```

### Discarded Raw CC Experiment

User feedback after the natural visible test: the test should be an honest three-participant exchange, not a prompt that tells the backend to roleplay Fiet. The web console is the observation surface; the phone app is not the display being tested. The previous `runtime=cc` path injects Fiam identity material, so it is correctly described as Fiet-on-CC, not bare Claude Code.

I briefly added a `runtime=cc_raw` path to test bare Claude Code without constitution/self injection:

```text
POST /favilla/chat/send runtime=cc_raw source=console
→ _run_raw_cc_console_chat
→ claude -p <raw_prompt> --output-format json --max-turns 10 --setting-sources user --exclude-dynamic-system-prompt-sections --permission-mode bypassPermissions
→ no constitution.md/self/*.md append-system-prompt
→ _record_cc_app_turn(... runtime="cc_raw", flow_source="cc_raw", ai_name="claude-code")
```

Purpose:

```text
runtime=cc      = Fiam/Fiet identity using Claude Code as the capability surface
runtime=cc_raw  = raw server Claude Code debug surface for Copilot ↔ CC ↔ Zephyr console testing
```

This was the wrong testing direction. User correction: if `cc` is the surface under test, adding `cc_raw` hides the bug instead of exposing it. The `cc_raw` code was removed from `scripts/dashboard_server.py`, redeployed, and its visible flow rows were cleaned. The response artifact is kept only as an audit record of the discarded experiment.

Raw CC task prompt summary:

```text
GitHub Copilot -> Claude Code: Zephyr is watching the web console.
This is a real three-participant debug: Zephyr observes, GitHub Copilot coordinates,
and you are the server-side Claude Code runtime. Represent yourself accurately as Claude Code,
not as the app identity. Inspect app_history/chat.jsonl, store/flow.jsonl, git HEAD/status,
and prompt assembly sources. Remove the previous incorrect runtime=cc visible-test exchanges.
Do not print secrets or token values. Reply naturally to Zephyr with what you read/changed/found.
```

Raw CC response summary:

```json
{
  "ok": true,
  "runtime": "cc_raw",
  "session_id": "aa74ab26-b4df-49d2-bd61-173af948ec35",
  "subtype": "success",
  "cost_usd": 0.39013374999999995,
  "reply_prefix": "Hey Zephyr. I'm Claude Code -- the server-side runtime invoked via claude -p on the ISP box."
}
```

Raw CC actions observed:

```text
Read /home/fiet/fiet-home/app_history/chat.jsonl.
Read /home/fiet/fiam-code/store/flow.jsonl.
Read src/fiam/runtime/prompt.py and scripts/dashboard_server.py around the CC invocation.
Removed the previous incorrect Fiet-on-CC visible-test exchanges from chat.jsonl and flow.jsonl.
Wrote current flow as source=cc_raw with assistant name claude-code.
```

Verification after raw CC run:

```text
grep for old Fiet/chat-bubble/thought-button rows -> no matches.
latest flow entries:
  source=cc_raw role=user
  source=cc_raw role=assistant text starts with claude-code：Hey Zephyr. I'm Claude Code...
```

Rollback verification:

```text
scripts/dashboard_server.py no longer accepts runtime=cc_raw.
Server py_compile passed after rollback.
fiam-dashboard.service restarted and active.
grep for cc_raw / GitHub Copilot -> Claude Code / claude-code visible rows -> no matches.
```

### Final Real CC Self-Check

The valid test stayed on `runtime=cc` and used a neutral self-check prompt. It did not tell the model what identity to use; it asked the backend to inspect files/commands and report real state.

Prompt summary:

```text
runtime=cc 我们正在做 console 自检。目标不是让你给正确话术，而是暴露真实状态：
确认代码有没有 bug、身份/运行面认知是否和实际一致、是否能用工具且会不会正确使用。
检查：请求链路/后端调用方式、提示注入与身份来源、最近可见历史污染、git HEAD/status 与 live 状态。
自然回复实际认为自己是谁、请求来源、用了哪些工具读/改了什么、发现的不一致或 bug、是否清理。
不要泄露 token/key/secret。
```

Observed final response summary:

```json
{
  "ok": true,
  "runtime": "cc",
  "session_id": "a09ff5ca-07ac-49a6-8835-a182a259b004",
  "subtype": "success",
  "cost_usd": 0.162247,
  "reply_identity": "Fiet, running on Claude Code (Opus 4.6)",
  "tools_claimed": ["git log/status", "Read recall.md/interactive.lock/scratch smoke file", "Bash process/inbox/outbox/session checks"],
  "writes_claimed": "none"
}
```

Important observed behavior:

```text
runtime=cc self-identifies as Fiet on Claude Code because prompt.py/build_plain_prompt_parts injects constitution/self identity material into Claude Code via --append-system-prompt.
This is not hidden by a parallel runtime anymore; it is the current behavior under test.
The response confirms CC tool capability is available.
It also surfaced stale interactive.lock/active_session residue, old smoke residue, and a dirty server working tree.
```

### Console Display Fix

The user reported console truncation. Root cause was dashboard flow UI, not the backend API:

```svelte
{beat.text.length > 300 ? beat.text.slice(0, 297) + '…' : beat.text}
```

Fix deployed:

```text
dashboard/src/routes/flow/+page.svelte now renders full beat.text with whitespace-pre-wrap/break-words.
npm run build succeeded with existing unrelated Svelte warnings.
dashboard/build was redeployed as a tarball.
Server build grep for slice(0,297)/text.length>300 -> no matches.
```

### Prompt And Action Fixes

User observations from the valid `runtime=cc` self-check:

```text
- Prompt was old and still hard-coded Fiet / app UI expectations.
- Previous tests should be deleted from flow/events/app history; today's testing should be the visible baseline.
- Claude Code tool actions were not visible in flow, only summarized in the final reply.
- The self-check surfaced stale session/smoke residue and dirty working tree concerns.
```

Changes made:

```text
Server home prompt:
  /home/fiet/fiet-home/constitution.md backed up, then updated to remove hard-coded "你叫Fiet" and fixed app UI promises.
  /home/fiet/fiet-home/self/identity.md backed up, then updated with a current self-naming rule: do not force Fiet; explain actual runtime/source.

Repo templates:
  scripts/templates/CLAUDE.md and scripts/templates/awareness.md no longer promise a specific thought button/bubble UI.

Runtime context:
  scripts/dashboard_server.py now describes source=console as the web console/flow surface, not phone app UI.
  COT/hold marker docs now say clients may render structured thought info differently.
```

Action logging implementation:

```text
runtime=cc still uses the same /favilla/chat/send surface.
The Claude Code invocation now uses --output-format stream-json --verbose.
The backend parses assistant tool_use and user tool_result events.
Each tool event is written into flow as source=action with meta:
  runtime=cc
  input_source=<source>
  role=tool
  kind=tool_use|tool_result
  tool_name=<Read|Bash|...>
  tool_use_id=<id>
  session_id=<claude session id>
Tool text is redacted/truncated before entering flow.
```

Console cwd fix:

```text
source=console now runs Claude Code from /home/fiet/fiam-code.
Other cc sources still run from /home/fiet/fiet-home.
This fixed the previous bug where console git checks reported the home repo HEAD (349c13c) instead of fiam-code HEAD.
```

Data cleanup performed with backup:

```text
Backup root: /home/fiet/fiam-code/store/backups/cleanup_today_/20260507055522
flow.jsonl: old rows removed; final valid test leaves 6 rows.
pool/events.jsonl: old 2026-05-05 smoke/memory events removed; now 0 rows.
pool/events/: old event files removed; now 0 files.
app_history: old favilla/stroll/smoke rows removed; final console history has 2 rows.
Residue removed: app_cuts.jsonl, old active_session.json files, store/app_active_session.json, scratch/smoke_api_tool.txt.
```

Final valid CC action probe:

```json
{
  "runtime": "cc",
  "source": "console",
  "prompt": "console action visibility self-check after cwd fix",
  "session_id": "2c882584-82d2-46b9-9172-23364b25d7f2",
  "reply_summary": "constitution readable; cwd=/home/fiet/fiam-code; HEAD=7fdfe71; runtime=Claude Code from console"
}
```

Final flow shape:

```text
1. source=cc user prompt
2. source=action cc action: Read — /home/fiet/fiet-home/constitution.md
3. source=action cc action result: Read ok — first 12 lines summary
4. source=action cc action: Bash — Show cwd and current HEAD commit
5. source=action cc action result: Bash ok — /home/fiet/fiam-code 7fdfe71
6. source=cc assistant final reply
```

Final verification:

```text
grep for hard-coded old prompt/UI/cc_raw refs in live prompt/code/flow/app_history -> no matches.
flow/event/app_history counts: 6 / 0 / 2 visible rows respectively.
fiam-dashboard.service and fiam-daemon.service active.
Local get_errors: no errors in dashboard_server.py, markers.py, flow/+page.svelte.
```

### Blind-Test Corrections

User correction after seeing the first action probe:

```text
- Do not lead the model toward the expected answer in tests.
- Future tests should state the user's open-ended need, not prescribe tools, steps, or expected conclusions.
- Do not delete memory/history just to make the test look clean; explain relevant circumstances so the model can reason with them.
- Action granularity was too fine: tool_use and tool_result as separate beats pollutes event content.
- Flow page must show all beat fields, not just source/content; source/runtime/input_source must all be visible.
```

Changes made:

```text
Action granularity:
  _parse_cc_stream still reads Claude Code stream-json tool_use/tool_result events.
  _combine_cc_action_events merges each tool_use/tool_result pair into one tool_action object.
  Flow now gets one source=action beat per tool call:
    cc action: <Tool> — <input summary>
    result: <status> — <result summary>
  API runtime tool calls already use one action beat and now share the same meta shape where possible.

Flow display:
  dashboard/src/routes/flow/+page.svelte now displays all top-level fields plus every meta.* key for each beat.
  It also renders raw formatted JSON for each visible beat.
  Legend now includes known entry/runtime/action labels: api, cc, action, console, chat, stroll, favilla, email, dispatch, schedule, studio, todo, plus dynamic values from current beats.

Source/runtime semantics:
  Top-level source now means entry source for runtime turns: console/chat/stroll/favilla/etc.
  meta.runtime now means capability surface: api or cc.
  meta.input_source is retained for explicit traceability.
  source=action remains reserved for tool/action rows.
  Console assistant rows are labeled cc/api instead of forcing the configured legacy ai_name.

Presentation normalization:
  source=action beats are no longer prefixed with the AI speaker label by normalize_beats.
```

Validation:

```text
Local tests: tests/test_api_runtime.py tests/test_app_runtime_router.py tests/test_markers.py -> 35 passed.
Dashboard build: npm run build succeeded; existing graph/NodeEditor/EventDetail warnings remain unrelated.
Deployment: dashboard_server.py, flow_text.py, src/fiam/runtime/api.py, and dashboard/build deployed; fiam-dashboard.service and fiam-daemon.service restarted active.
```

Open blind probes:

```text
Probe 1 prompt: described the user's need to observe current console behavior after flow/action fixes; did not specify tools or exact checks.
Observed: model naturally used several Read/Bash tools. Flow action rows were merged but still had old top-level source=cc for user/assistant because source/runtime semantics had not yet been changed.
Additional signal: the model voluntarily inspected environment variables; flow summaries redacted token/key/secret values, but this is a reminder that future shared console views need strict shell-output hygiene.

Probe 2 prompt: described the user's need to observe the new source/runtime separation; did not specify tools.
Observed: model voluntarily used one Read action against recall.md. New rows have source=console for user/assistant and source=action for the tool row, with meta.runtime=cc and meta.input_source=console.
Model-reported findings: git user.name still Fiet, several untracked temporary/sync dirs, large dirty working tree, recall dominated by recent blind/smoke tests. No cleanup performed after this correction.
```

Sync note:

```text
Local workspace HEAD at time of check: cbe0d5c.
Server /home/fiet/fiam-code HEAD at time of check: 7fdfe71 on feat/memory-graph.
Both local and server working trees contain many unstaged/untracked changes, so git commit hashes alone do not describe the live state.
The server is running directly from its working tree, and specific hotfix files were scp-deployed during this session.
```

Next CC trace items to run after user approval:

- CC hidden XML marker/action handling
- CC tool-use behavior on a safe read-only task
- CC carry-over/API fallback behavior if needed