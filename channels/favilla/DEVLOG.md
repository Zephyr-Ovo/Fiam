# Favilla DEVLOG

> Favilla app-specific notes. Root project/system notes stay in `../../DEVLOG.md`.

## ⚠️ 操作语义 LOCKED — 每次开工必读
**剪刀**=弹窗确认后才落 cut marker `/api/app/cut`；剪刀本身不触发 DS 处理。
**沙漏单击**=toggle recall armed（亮↔暗；亮+下次发送=带召回；点发送或再次单击=灭）。
**沙漏长按 1.2s**=弹窗确认→`/api/app/process`（3 阶段 DS 管线，沙漏漏沙动画 + 发送灰，完成信号到才解锁）。
**键盘**：按钮操作不改变当前键盘状态；键盘展开时点发送/剪刀/沙漏/加号/语音/返回都不能先收键盘，返回直接离开当前页。
**Chat history**：聊天记录以 server `/api/app/history` 为准；App 不再 seed mock 测试会话。退出、重进、卸载、重装后应从服务器恢复历史。
**Upload**：纯上传只把文件落到服务器 `uploads/` + `uploads/manifest.jsonl` 并记录 history，不唤醒 AI、不把全文注入上下文。后续 AI 只看近期上传清单（路径/文件名/mime/大小），需要自己用 grep/read 工具找内容。
**Backend Settings**：`AI` = server auto-router（代码/附件/debug 等走 CC，日常短聊走 API）；`API`/`CC` = 用户手动强制后端，此状态下 AI 不能自行切换，除非用户改回 `AI` 或手动选择另一项。
**Settings**：背景 `rgba(0,0,0,0.45)` 纯变暗不模糊；卡片 `backdrop-filter: blur(20px)` + `rgba(255,250,243,0.55)` 半透明磨砂；居中固定，CSS-only fade 120ms。

## Session 2026-05-04 — Persistent chat + phone smoke

- Commit `21959c6` built by GitHub Actions run #53 and installed on phone `OVOJUWYD4HIZYHKZ`; old package signature mismatch required uninstall + reinstall.
- Build-time `VITE_INGEST_TOKEN` secret was missing/invalid in Actions, so phone Settings/localStorage was patched with live `apiBase=https://fiet.cc` + ingest token via WebView DevTools. Fix Actions secret before the next clean APK build.
- Phone verified: empty server history on fresh start; send keeps keyboard open; `+` keeps keyboard open; header back exits Chat directly; scissor opens confirmation and writes cut divider only after confirm.
- Phone verified: server-backed history restores after app force-stop/relaunch; pure upload adds one user attachment + upload manifest row and does not wake AI.
- Phone verified: auto daily chat routes to API; forced CC via Settings/localStorage routes through Claude Code. After smoke, active history was archived/removed, active CC session cleared, and cut markers reset to 0.

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
