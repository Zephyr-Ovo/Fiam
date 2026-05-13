# Favilla DEVLOG

> Favilla app-specific notes. Root project/system notes stay in `../../DEVLOG.md`.

## ⚠️ 操作语义 LOCKED — 每次开工必读
**剪刀**=弹窗确认后才落 cut marker `/api/app/cut`；剪刀本身不触发 DS 处理。
**沙漏单击**=toggle recall armed（亮↔暗；亮+下次发送=带召回；点发送或再次单击=灭）。
**沙漏长按 1.2s**=弹窗确认→`/api/app/process`（3 阶段 DS 管线，沙漏漏沙动画 + 发送灰，完成信号到才解锁）。
**键盘**：只有 textarea 目标区域负责拉起键盘；普通工具操作保持当前键盘状态（展开则保持，收起则不弹出）；发送保持键盘以便连发；传文件/剪刀/沙漏/语音/思考链展开不主动弹键盘也不收键盘；键盘已开时只有点聊天背景空白区收键盘；返回直接退出会话并收键盘。
**Chat history**：聊天记录以 server `/api/app/history` 为准；App 不再 seed mock 测试会话。退出、重进、卸载、重装后应从服务器恢复历史。
**Upload**：纯上传只把文件落到服务器 `uploads/` + `uploads/manifest.jsonl` 并记录 history，不唤醒 AI、不把全文注入上下文。后续 AI 只知道 manifest 路径；只有当前消息明确需要文件/图片时才自己用 grep/read 工具找内容。
**Backend Settings**：`AI` = server auto-router（同一个 AI 身份可按任务在 API/CC 能力面间切换）；`API`/`CC` = 用户手动强制本轮请求后端，AI 仍应理解这是 transport/capability surface，不是身份变化。
**Settings**：背景 `rgba(0,0,0,0.45)` 纯变暗不模糊；卡片 `backdrop-filter: blur(20px)` + `rgba(255,250,243,0.55)` 半透明磨砂；居中固定，CSS-only fade 120ms。

## Session 2026-05-13 — Runtime/settings/object transfer

- Chat now sends `appConfig.defaultRuntime` to the Favilla SSE endpoint, so Settings-forced `API`/`CC` actually overrides auto routing.
- Settings dialog keeps a fixed viewport-bounded size and scrolls its internal form area instead of growing beyond the screen.
- Follow-up: Settings was shortened to a 520px viewport-bounded panel; its internal scroll keeps touch sliding but hides visible scrollbars.
- Streaming text deltas are joined back into one visible bubble, and backend deltas preserve whitespace from Claude Code output.
- Thinking/task icon inference now respects explicit/native icon hints first; search steps map to the search icon instead of file-text.
- Official/native Claude Code thinking now goes through the same summary helper as `<cot>`/Fiam thinking; the UI labels collapsed rows as `Native thinking` vs `Shared thought` so both chain types are visible.
- AI replies can now return downloadable ObjectStore attachments when the visible reply contains `obj:<hash/prefix>`; file/image pills with `object_hash` call `/favilla/object/<token>`.
- Pure upload transcript writes now preserve `object_hash`, so server-restored upload pills can also download the stored object.
- ISP `fiam-dashboard.service` now grants write access to `/home/fiet/.claude.json` as well as `/home/fiet/.claude`; forced CC works from systemd after restart instead of returning Claude Code 401 while SSH succeeds.
- Verification: Favilla app build passed; focused backend regression tests for stream spacing, object token extraction/download, upload, transcript persistence, and receive trace passed.

## Session 2026-05-11 — Chat usability / console cleanup

- Removed the old 60s client-side send batching path from Chat. A normal Send/Enter now immediately starts `/favilla/chat/send` SSE; the old second-empty-send flush behavior is gone.
- Chat remains pre-mounted for transition smoothness, but network side effects are now gated by visibility: transcript fetch and computer-control EventSource only start when Chat is active.
- `VITE_API_TARGET` is no longer used as the browser API base. It remains a Vite proxy target; the app uses `VITE_API_BASE` or same-origin relative `/favilla/*` calls.
- Dashboard, Stroll, and Studio are no longer mounted while hidden behind Home/Chat, preventing Recharts zero-size warnings, Mapbox fit warnings, hidden Limen stream attempts, and BlockNote font noise during normal Chat use.
- Removed leftover debug console output from Chat/API path and added an accessible name to the Send button.
- Verification: Favilla app build passed; key chat backend tests passed (`stream_persists_transcript_before_done`, shared transcript routing); browser smoke with mocked `/favilla/*` confirmed Home makes no Favilla requests, Chat loads only after open, Send posts within ~30ms, streamed reply renders, and no app warnings/errors appear in console. Full app lint still has unrelated existing errors in `lib/limen.ts`, `routes/Settings.tsx`, and `routes/Stroll.tsx`; not touched because ring/Stroll work is parallel.

## Session 2026-05-06 — BTW Dashboard/Studio mainline wiring

- Dashboard and Studio were moved into `channels/favilla/app` from the BTW prototype folders with the original UI/style preserved. Do not redesign these pages while wiring functionality; keep visual changes to compatibility fixes only.
- Added server-backed Favilla Dashboard summary at `GET /favilla/dashboard`, using the ingest token and combining Fiam status/health, recent pool events, todo queue, chat digest, and Stroll digest. Colmi ring metrics remain placeholder-only until a mainline BLE/Colmi backend exists.
- **2026-05-11 Ring integration**: Added `GET /ring/today` (reads `store/wearable/ring_today.json`) and `POST /ring/sync` (writes ring data) to `dashboard_server.py`. `_favilla_dashboard()` now includes `ring` field. `DashboardSummary.ring?: RingTodayData` added to `api.ts`. Dashboard Heart card wired to `summary.ring?.current_hr` / `resting_hr`; Activity card wired to `summary.ring?.steps`. Created `scripts/sync_ring.py` to read today's data from colmi SQLite DB and POST to server. Usage: `python scripts/sync_ring.py --db <path/to/ring.db> [--server <url>] [--token <token>]`
- **2026-05-11 Mobile BLE sync**: Added `@capacitor-community/bluetooth-le` (v8.1.3). Created `src/lib/ring-ble.ts` implementing Colmi R02 BLE UART protocol: scans for ring name prefix `R11C`, connects, reads real-time HR (CMD 0x69, collects ≥3 non-zero readings) and today's steps (CMD 0x43, multi-packet state machine), POSTs to `/ring/sync`. Dashboard header now has a Watch icon button (idle/syncing/ok/error states) that triggers `syncRingToServer()` and refreshes on success. Ring advertised BLE name: `R11C_2603`.
- Added server-backed Studio state at `GET/POST /favilla/studio`, stored under `home_path/app_studio/state.json`. The app loads this state on mount and debounced-saves files, active note content, active file, and timeline to the server; it is not phone-local-only.
- Vite dev now proxies both `/api` and `/favilla` to `VITE_API_TARGET`/`127.0.0.1:8766` so local development can use the same relative Favilla endpoints.
- ISP Caddy now bypasses basic auth for `/favilla/*` and reverse-proxies it to the dashboard server; public CORS preflight for `/favilla/studio` returns 204 and live public reads for `/favilla/dashboard` + `/favilla/studio` return `ok: true`.
- Verification: `py_compile scripts/dashboard_server.py`, `npm --prefix channels/favilla/app run build`, VS Code diagnostics, browser smoke for Dashboard open/back plus Studio open/file drawer/back, ISP dashboard restart, public CORS preflight, and public endpoint reads passed.

## Session 2026-05-06 — Stroll marker details + Studio/Dashboard linkage

- Stroll map annotations now keep spatial record metadata: emoji/text, source/origin, place kind, distance/radius, attachment URL, and created/updated timestamps. Clicking an AI marker opens a detail card with emoji, description, date, place/source, distance, and photo/attachment context.
- Dashboard now consumes backend `studio` and `locations` digests. AI/human ratio, emoji calendar, and creative footprint include Studio timeline activity plus Stroll spatial records, so location-linked writing shows up beside walk markers.
- Studio now persists per-file contents and richer timeline events. Manual edits, new notes, AI edit requests, suggested edit scripts, and applied AI edits are recorded with file, unit count, timestamp, and optional browser location.
- Studio AI editing uses command scripts, not whole-document replacement. Backend `/favilla/studio/edit` asks API/CC for JSON operations (`replace`, `insert_after`, `insert_before`, `delete`, `append`, `prepend`), and the frontend applies them locally so timeline/attribution can say what changed. User decision: Studio edits do not enter Flow yet; they only update Studio state/timeline and `app_history/studio.jsonl`.
- Verification: focused Python suite `tests.test_app_runtime_router tests.test_stroll_store tests.test_api_runtime` passed (26 tests); `npm --prefix channels/favilla/app run build` passed; VS Code diagnostics are clean. Local browser smoke on a temporary 8767 server verified Stroll marker popup emoji/date/place/source/distance, Dashboard Studio/location summaries, and Studio AI edit generate/apply/timeline behavior with a stubbed edit response.
- Real Studio model smoke on temporary 8767 passed: instruction `AAA -> AABA` returned one `replace` command (`target=AAA`, `text=AABA`) via API model `anthropic/claude-3-5-haiku`, cost about `$0.001772`. Smoke `app_history/studio.jsonl` rows were removed afterward.

## Session 2026-05-06 — Limen/XIAO camera execution

- Stroll action XML now includes `view_camera` alongside `capture_photo`, `set_limen_screen`, and `refresh_nearby`.
- Settings no longer collects OpenRouter/API keys. API runtime keys live in server/local environment variables; the app only keeps Favilla API token/base plus `Limen URL` for the local XIAO device.
- Added a Limen local client for `GET /health`, `GET /stream`, `GET /capture`, and `POST /screen`.
- Stroll Live shows the Limen MJPEG stream; Photo shows the latest still. AI `capture_photo` captures from XIAO, uploads through `/favilla/upload`, writes a Stroll `photo` record with emoji/description/location metadata, and sends the uploaded image back to AI as an attachment for review.
- AI `set_limen_screen` mirrors short text to XIAO `/screen`; the small round Limen preview in Stroll mirrors the latest requested screen content.
- Mainline `channels/limen` firmware is now local camera/screen HTTP instead of server polling. Touch reset is three taps within seven seconds while awake; a sleeping display's first touch only wakes the screen.

## Session 2026-05-06 — Todo / held reply protocol cleanup

- App-facing hold metadata is now `hold.queued`, matching backend `held_reply` todos. The old queue naming should not reappear in Favilla types or server responses.
- Backend shared transcript is the API ↔ CC context bridge for the same AI identity; app history stores the visible chat transcript separately.
- Verification: `npm run build` passed in `channels/favilla/app`; Python app-backend tests cover shared transcript routing.

## Session 2026-05-06 — Stroll source foundation

- Stroll now has real Favilla source routes instead of static-only UI: `/favilla/stroll/send`, `/favilla/stroll/history`, `/favilla/stroll/nearby`, `/favilla/stroll/records`, and `/favilla/stroll/action-result`.
- Server-side spatial records are point-only and stored under `home_path/stroll/`; nearby lookup uses rough 50m cells for archive selection, then true-distance <=50m filtering.
- Stroll send injects current location/cell/place/nearby records as private runtime context only for source `stroll`; stored Stroll history remains clean user/AI chat text.
- The Stroll page now loads source history, watches phone geolocation while active, sends text through the Stroll route with context, writes user/AI point notes, and renders nearby photo/note records as map annotations.
- Verification: focused Python suite `tests.test_app_runtime_router tests.test_markers tests.test_api_runtime tests.test_stroll_store` passed; `npm --prefix channels/favilla/app run build` passed with existing chunk-size/plugin-timing warnings.
- ISP real runtime verification: after surgical sync, Python focused suite passed remotely; direct Stroll send smoke passed on both `runtime=api` and `runtime=cc`. Smoke rows were removed from Stroll app history and flow, and the smoke CC active session was cleared. Live HTTP `/favilla/stroll/history` and `/favilla/stroll/nearby` returned `ok: true` after dashboard restart. Claude Code is ISP-only (`ssh isp`, `/usr/bin/claude`); do not use local Windows Claude Code for Favilla runtime validation.
- AI map marking is active: API and CC can emit hidden `<stroll_record kind="marker" text="..." />` markers, which the server stores as Stroll spatial records and strips from the visible reply/history. Real ISP marker smoke passed for both runtimes and `/favilla/stroll/nearby` read the written markers back; smoke artifacts were removed afterward.
- Rough place semantics are active when Mapbox is available: `StrollMapView` samples features around the current point and reports `road`/`green`/`building`/`water`/`unknown` into Stroll context.
- Stroll tool/action XML is active: API and CC can emit hidden `<stroll_action type="capture_photo" />`, `<stroll_action type="set_limen_screen" />`, and `<stroll_action type="refresh_nearby" />`. The server strips XML from visible text, queues actions, and returns `stroll_actions`; the app renders compact tool rows with Camera/Monitor/Refresh icons, refreshes nearby records immediately, and best-effort reports action results. Hardware execution remains deferred while the phone/Limen device is absent.
- Real ISP three-action smoke passed for both API and CC: marker + all three actions returned, raw XML absent from visible replies, action statuses queued, and smoke history/spatial/action/flow rows were cleaned. Web preview on local Vite `5175` used routed mock Stroll responses; Playwright confirmed the three action rows (`photo request queued`, `Limen screen queued`, `nearby refreshed`) render without breaking the Stroll conversation stack. Build still passes with existing chunk/plugin warnings.

## Session 2026-05-06 — Figma Streamline icon cache

- Cached Figma node `71:1291` from `favilla-v2` locally under `channels/favilla/docs/figma/`: full metadata XML, board screenshot PNG, and selected-node manifest.
- Selected 43 likely Favilla action icons for Home/Studio/Chat. 42 nodes are verified SVG-exportable; `magic-wand` is present in metadata but Figma reports no visible exportable layers.
- Recovered 34 complete SVG files from two selected MCP output batches before the 20KB tool truncation point into `channels/favilla/assets/icons/streamline/`. Use `docs/figma/favilla-v2-icons-71-1291.selection.json` as the full node index.
- Copied the recovered SVG set into Favilla app public assets and wired Chat thinking/task rows to infer more specific icons from step icon/summary/text/result/source. Keep user-approved custom controls (recall/hourglass) unchanged.
- Verification: `npm --prefix channels/favilla/app run build` passed; local Vite preview served the Streamline SVG paths used by the Chat thinking/task rail.

## Session 2026-05-04 — Persistent chat + phone smoke

- Commit `21959c6` built by GitHub Actions run #53 and installed on phone `OVOJUWYD4HIZYHKZ`; old package signature mismatch required uninstall + reinstall.
- Build-time `VITE_INGEST_TOKEN` secret was missing/invalid in Actions, so phone Settings/localStorage was patched with live `apiBase=https://fiet.cc` + ingest token via WebView DevTools. Fix Actions secret before the next clean APK build.
- Phone verified: empty server history on fresh start; send keeps keyboard open; `+` keeps keyboard open; header back exits Chat directly; scissor opens confirmation and writes cut divider only after confirm.
- Phone verified: server-backed history restores after app force-stop/relaunch; pure upload adds one user attachment + upload manifest row and does not wake AI.
- Phone verified: auto daily chat routes to API; forced CC via Settings/localStorage routes through Claude Code. After smoke, active history was archived/removed, active CC session cleared, and cut markers reset to 0.

## Session 2026-05-03 — Chat keyboard semantics pass

- Fixed tool controls to behave like a normal chat app: `+`/scissor/hourglass/voice do not summon the keyboard when it is closed, and preserve it when already open.
- Header back now blurs the active input and exits Chat in one step. Tapping the message area outside the composer closes the keyboard and attachment menu.
- Restored lightweight client-side typewriter rendering for AI replies without changing the backend contract.

## Session 2026-05-04 — Keyboard simplification + share image

- Simplified chat keyboard rule: buttons never actively blur/focus the composer. Only tapping the message area outside controls or leaving Chat dismisses the keyboard.
- Added long-press bubble selection. In selection mode the header switches to cancel/share; share generates a cream chat-style PNG with visible thinking chains expanded when available.
- Reply rendering now uses softer grapheme chunk typing instead of one-shot full reply.
- Server prompt context now tells both API and CC paths that Favilla `AI` mode is automatic routing across API/CC surfaces for the same AI identity.
- Live cut/process check: `app_cuts.jsonl` was empty after processing, `annotation_state.processed_until` matched current flow length, and pool event files existed under `store/pool/events/`.

## Session 2026-05-04 — Follow-up keyboard/prompt pass

- Attach menu backdrop/cancel now only closes the menu; it does not blur or otherwise alter keyboard state.
- AI reply typing slowed slightly (`42ms` chunk cadence) for a gentler typewriter feel.
- Disabled per-tap Capacitor native haptics bridge; it caused visible first-tap jank on controls. Browser vibration remains best-effort.
- Confirm modal remembers the largest screen height so soft-keyboard viewport shrink is less likely to recenter the dialog into the keyboard-free area.
- App prompt no longer injects concrete recent upload rows every turn. Backend context only tells AI where `uploads/manifest.jsonl` lives and to inspect files only when relevant.
- Live constitution updated: Favilla is the primary direct channel, and `AI` means auto routing across API/CC surfaces.

## Session 2026-05-04 — Keyboard focus + backend proof pass

- Chat background blur is now restricted to actual blank `<main>` hits while the keyboard is visible. Tapping bubbles, thinking-chain controls, cut/process modal buttons, attachment controls, or send/share buttons must not alter keyboard visibility.
- Added a document-level stale-focus release: if Android WebView leaves the textarea focused after the soft keyboard is already hidden, the next non-textarea pointerdown blurs it so controls cannot resurrect the keyboard.
- Chat auto-scroll is pinned only while the user is already at the live tail. If the user scrolls up with the keyboard open, thinking-chain expand/collapse must preserve keyboard state and must not snap the list back to the bottom.
- Auto-router no longer treats generic `api`/`backend` mentions as CC work. Explicit `backend=api`, `backend: api`, API/CC mode phrases, short `去/到/用/走 api|cc` phrases, and `另一边/切换过去` phrases are handled directly.
- New app history rows record the actual selected `backend` for both user and AI messages. Backend prompt context now treats `[app:... backend=api|cc]` as authoritative.

## Session 2026-05-04 — Stroll first layout

- Home `walking` target now opens a `Stroll` route instead of logging a placeholder. Stroll uses the same mounted slide shell as Chat and returns Home with the same back behavior.
- First static Stroll layout: compact header, 4:3 camera stage, Xiao circular screen preview at camera/map boundary, lower map surface, translucent livestream-style conversation text directly over the map, and bottom composer/call/fold controls.
- Stroll palette intentionally diverges from Chat, using muted peach, ink blue, grey-lilac, aqua, and slate references from the provided palette strips rather than copying them directly.
- Revised Stroll interaction: enter only by pulling the Home top stroll text, because broad Home drag was too easy to trigger. Stroll sits above Home and folds upward with the bottom `^` control; top-left square is End and must confirm.
- Revised Stroll controls: Xiao screen overlaps the camera/map boundary on the left; Live/Photo is a single two-half switch button on the right; the bottom row has text input, hold-to-record Voice, send, separate Call, and fold.
- Stroll Voice is press-and-hold recording: while held, the composer becomes an animated thin-bar waveform/release state, and the eventual sent message should display as STT text. Stroll Call is a separate persistent realtime state that replaces the bottom composer only, with timer, animated waveform, record-dot button, and hang-up button; map messages remain visible during calls.
- Stroll map conversation uses content-width bubbles with a shortened max boundary aligned to the conversation area: short messages do not fill the row, long messages wrap, and the stack sits in the safe band between the Xiao preview and bottom controls.
- Moved the Stroll Mapbox reference out of temporary `btw/` and Favilla-local source into shared `packages/stroll-map`. Favilla consumes it via the `@stroll-map` alias, while resolving `react`, `mapbox-gl`, and `gcoord` from `app/node_modules`.
- Temporarily added a Stroll layout tuner, read the user's adjusted values, then removed the tuner and baked the tuned layout into code: no `limen live` subtitle, transparent End icon, camera fixed at 4:3, tighter Xiao/map bridge, shortened chat bubbles, and bottom composer aligned to the chat column with Call separated from the fold button.
- Latest Stroll Call semantics: Call is styled as an input-side action, not like the fold button. Once connected, call recording is a bare external record dot beside the call strip; tapping it expands into the same glass style as the input strip, with pause/resume + stop/save buttons on the left and recording time on the right. Stop creates timestamped `.mp3` metadata in `localStorage` (`favilla:stroll-call-recordings:v1`) as a front-end stand-in until native phone audio capture/storage is wired.
- Stroll Mapbox integration now applies local actual time to Mapbox Standard `lightPreset` (`dawn`/`day`/`dusk`/`night`) and fetches weather for the sample route's current point via Open-Meteo by default, or `VITE_STROLL_WEATHER_ENDPOINT` when provided. `VITE_MAPBOX_TOKEN` is required for the live Mapbox basemap; without it the built-in fallback map remains visible. Local preview verified on `5174` with a real `.mapboxgl-canvas` and no fallback SVG after writing `VITE_MAPBOX_TOKEN` to ignored `app/.env.local`.
- Vite env loading is pinned to the Favilla app directory (`loadEnv(..., __dirname, ...)` + `envDir: __dirname`) so local `.env.local` is picked up even when commands are launched from the repo root via `npm --prefix`.
- `btw/` is no longer a source of truth. Source/config files were cleared after migration; if `btw/node_modules` remains, it is only because Windows is holding a native Rolldown binary lock and can be removed after the locking process exits.
- Latest Stroll map semantics: still 2D. The dark look comes from local-time Mapbox Standard night/dusk presets, not 3D. Default labels/POIs/3D objects are suppressed; the shared map keeps boundaries/roads as muted spatial data and overlays AI-owned labels such as where we turned, flower/photo notes, and slick paving.
- Stroll now has a page-level weather curtain so rain/snow can animate across the whole Stroll surface, not only inside the map. The map still receives weather for Mapbox-native rain/snow when supported.
- Stroll top-right control expands/collapses the map inside the Stroll page so the map covers the full Stroll root while conversation/composer stay stable above it. Do not hide Android system bars globally in `MainActivity`; Home must keep its existing proportions and the escape/strollentry image must stay at its original visual size even if the pull hit target is larger.
- Android route entry correction: the Home Stroll handle now supports tap as well as pull because installed WebView/ADB swipes did not reliably trigger the pointer pull path. Do not call `StatusBar.hide()` from `Stroll.tsx` while Shell pre-mounts routes; it hides Home's status area before navigation. Keep Stroll fullscreen as an in-page map expansion until route lifecycle is made truly conditional.
- Stroll fullscreen belongs in `Shell.onHomeNavigate("walking")` and Stroll `onBack`: entering Stroll hides StatusBar / requests fullscreen from the user gesture, exiting restores it. Do not put this side effect inside pre-mounted Stroll components.
- Stroll map expand button uses a larger 48dp hit target, and conversation bubbles are lifted farther above the composer in both normal and expanded map modes.
- Mapbox logo/attribution controls are moved off-canvas with CSS, and the shared Mapbox component observes container resizes so expanded map mode repaints to the new full-screen bounds instead of only shifting position.
- Tapping Stroll conversation bubbles hides the conversation stack; the small `^` chip restores it.
- APK deploy should use GitHub Actions, not local Android tooling. `scripts/deploy_favilla.ps1` can dispatch `favilla-android.yml` with a local Mapbox public token input so the built APK uses the real Mapbox basemap without committing `.env.local`.
- Stroll layout refinement: screen/camera and map expansion are separate in-surface controls. Conversation records are a root overlay above whichever surface is expanded; they auto-scroll to the newest row, can be scrolled back during the same Stroll, and clear when leaving the Stroll route. Bottom controls stay present; input flexes wider while Call is a compact send-sized action.
- Stroll map is now pure 2D in `packages/stroll-map`: removed the old `2d|3d` mode prop, pitch/bearing branches, tail/footstep live-mode layers, and 3D basemap toggles. Current position is a standard pin, tapping it recenters/zooms like a navigation locate action, and pace color is fixed purple -> pink -> yellow -> red from walking through cycling speeds with faster transit clamped at red.
- Map annotations now have shared structured contracts: photos merge into one dot within 20m via `buildPhotoAnnotations`, AI annotations render as white droplet emoji pins, and `StrollSpatialContext` is the intended AI input for route points, markers, weather/light, and nearby memories instead of relying on screenshots.

## Session 2026-04-30 — Hard Reset to React + Tailwind + shadcn

**Trigger:** All previous attempts (XML → Compose → SvelteKit + Workspace Engine) hit the same wall: design-to-implementation lossiness produced "plastic-feeling" UI. Tech-stack churn was a symptom, not the cause. Real bottleneck: AI reading PNG screenshots loses spacing/typography/layer info, and default component libraries (any of them) feel like SaaS dashboards.

**Decision:** Start over. Functional scope unchanged (Chat, Read, Stroll). New stack chosen for "ease of writing beautiful screens", not popularity.

### Stack
- Vite + React 19 + TypeScript
- Tailwind v4 (`@tailwindcss/vite` plugin, `@theme` tokens in `src/index.css`)
- shadcn/ui (new-york style, neutral base, lucide icons), `components.json` configured
- framer-motion for transitions
- Capacitor 7 wrapping Android (`cc.fiet.favilla`, web-dir `dist`, `android/` platform added)
- Anthropic Serif/Sans/Mono fonts bundled in `src/assets/fonts/`

### Removed (gone, not archived)
- `channels/favilla/app/` (old Kotlin/Compose Android project)
- `channels/favilla/web/` (empty SvelteKit skeleton)
- `channels/favilla/archive/` (legacy Figma+Compose docs)
- `channels/favilla/docs/` (`paper_app.md`, `workspace_engine.md`)
- `channels/favilla/assets/` (paper-pile PNGs, no longer relevant)
- `packages/workspace-engine/` (shared engine package, not needed)
- `.github/workflows/favilla-android.yml` (will be rewritten when CI is needed again)

### Kept untouched (must not be deleted)
- `dashboard/` — fiet.cc desktop console, unrelated to favilla
- `tlon/` (sibling repo to fiam-code) — separate project
- Backend `/api/app/*` endpoints in `scripts/dashboard_server.py` — to be reused later when frontend is ready to reconnect

### Design references
- `channels/favilla/refs/page/` — vibe references (literary / paper / collage)
- `channels/favilla/refs/dark/` — dark-theme starlight inspiration
- `channels/favilla/refs/color/` — color palettes
- `channels/favilla/refs/design/` — layout creativity
- `channels/favilla/refs/crow/` — F = bird = crow, may use as logo motif
- `channels/favilla/refs/font/` — Anthropic Serif/Sans/Mono TTF (bundled into app)
- Figma file `TfPKdejmmDQVs7WMyfpERj` (favilla-v2) — empty new file as design source
- Old Figma file `26iqkLyW9LMDIASBJMo5jj` — frozen; may pull individual icons via MCP if needed

### Workflow
1. **Vibe anchoring**: build 2-3 Chat-screen visual variants in code, user picks one.
2. **First screen**: complete Chat layout (bubbles, composer, header, entrance animation).
3. **Interaction**: input, message rendering, scroll, keyboard handling — all on mock data.
4. **Backend**: swap mock for real `/api/app/chat`.
5. Repeat for Read, then Stroll.

Figma is reference-only, not a required pipeline. Design lives in code.

### Status
- Skeleton: Vite+React+TS+Tailwind+shadcn+Capacitor scaffolded; `vite build` passes.
- Android: `android/` platform added; not yet built into APK.
- First screen: not started; pending vibe-anchoring step.

## Session 2026-05-01 — Home collage + Settings sheet + 7-event window

### Architecture
- Split monolithic `App.tsx` into:
  - [src/Shell.tsx](app/src/Shell.tsx) — outer wrapper + phone frame (412×915 dp, matches Reno 8 viewport) + page state machine `home | chat`.
  - [src/routes/Home.tsx](app/src/routes/Home.tsx) — absolute-positioned collage from Figma `TfPKdejmmDQVs7WMyfpERj` node 8-1290; PNG assets in `app/public/home/`. Hit boxes decoupled from visuals (`DEBUG_HITBOX` flag in source).
  - [src/routes/Settings.tsx](app/src/routes/Settings.tsx) — iOS-style: full-page light blur (10px / saturate 105%) + 78%-height bottom sheet with grouped frosted cards (Names left/right, Backend solo). No `default backend` field. Save fires `favilla:config-changed` CustomEvent (no reload).
  - [src/App.tsx](app/src/App.tsx) — now an embeddable Chat page; takes `onBack` prop, listens to `favilla:config-changed` for live `peerName` updates.
- Home button hierarchy: setting / strollentry / chat (Poke me!) / gallery / studio (Brewing) / dashbroad (To-do) / eastenegg (>p0). Easter button rendered last → covers others.
- All home tile clicks have 240ms press-spring delay before navigation so tap animation completes.
- `viewport-fit=cover` on `index.html`; chat header has `paddingTop: env(safe-area-inset-top)` for status-bar overlay.

### UX fixes
- ConfirmModal: removed `mx-6`, frosted center alignment.
- HourglassIcon: pure CSS `scaleY` drain/fill animation, gold sand `#FAEC8C`, no rotation.
- Recall toggle: tap arms locally (gold fill); `recallNow()` called once at start of `handleSend()` if armed; send always disarms.
- "thought silently" → `${peerName} thought silently` (reactive to settings rename).
- Chat composer: textarea on top + 4-icon mirror row (`+` / Recall left, Mic / Send right), all `h-9 w-9`.
- User-message bubble shows ⭐ overlay if `recallUsed=true`.

### Chat windowing
- Chat now renders only the **last 7 sealed blocks** (cut-bounded) plus the live unsealed tail. Logic at the start of the `<main>` map: collect scissor-divider indices, drop oldest if count > 7.
- `SHOW_BLOCKS` constant in [App.tsx](app/src/App.tsx) line ~755.

### CI / APK pipeline
- Added [.github/workflows/favilla-android.yml](../../.github/workflows/favilla-android.yml):
  - Triggers: push to `feat/memory-graph` (paths under `channels/favilla/app/**`) + `workflow_dispatch`.
  - Steps: Node 20 + JDK 17 → `npm ci` → `npm run build` (`VITE_API_BASE`/`VITE_INGEST_TOKEN` from secrets) → `npx cap sync android` → `./gradlew assembleDebug` → upload `app-debug.apk` artifact.
  - **Required GitHub secrets:** `VITE_API_BASE` (e.g. `https://fiet.cc`), `VITE_INGEST_TOKEN`.
  - Local install: download artifact ZIP → unzip → `D:\scrcpy-win64-v3.3.4\adb.exe install -r app-debug.apk`.

### Pending
- Wire CC backend through `Conductor.receive_cc()` so seal/recall pipeline applies to both backends.
- Implement real `POST /api/app/cut` (current seal stub creates random fingerprints).
- Wire other home tiles (moments / dashboard / reading / walking / easter) to placeholder pages.
- Optional: swap home `bg.png` (user said "那盆花不太合适").
- Optional: strollentry → swipe-down gesture instead of tap.

## Session 2026-05-01 — Chat polish corrections

- Page transitions: Home and Chat stay mounted; navigation uses horizontal translate slide. System/back and header back return to Home; Home back minimizes app via Capacitor instead of exiting.
- Composer bottom memory control is one hourglass button again: tap toggles yellow armed state, four taps within ~1.4s runs the real seal path and the hourglass animates until `sealEvent()`/DS processing returns. No fake timer-driven burst and no separate extra hourglass button.
- User message recall marker uses the provided sparkle SVG as an absolute corner badge; composer itself should not use the sparkle icon.
- Hourglass icon shape is the user-provided 12×14 SVG (two bulbs + top/bottom rails), white at rest and pale gold when armed/animating. Copy affordance is transparent icon-only, not a white circular chip.
- Sends are batched client-side: consecutive user sends within 60s render immediately in chat but flush to `/api/app/chat` as one combined payload after the window, or immediately if Send/Enter is pressed while the input is empty and a batch is pending.
- Performance: Chat is pre-mounted behind Home; per-bubble `backdrop-filter` removed because Android WebView was visibly stalling when entering Chat.
- Multi-select direction: long-press any bubble enters selection; selected messages show left-side dots. Export target is poster/image only for now (no Markdown/JSON sidecar).
