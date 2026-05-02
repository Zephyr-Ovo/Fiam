# Favilla DEVLOG

> Favilla app-specific notes. Root project/system notes stay in `../../DEVLOG.md`.

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
- "Fiet thought silently" → `${peerName} thought silently` (reactive to settings rename).
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
- Composer bottom memory control is one hourglass button again: tap toggles yellow armed state, four taps within ~1.4s triggers a short sand-flow burst and immediate `recallNow()` refresh. No separate extra hourglass button.
- User message recall marker uses the provided sparkle SVG as an absolute corner badge; composer itself should not use the sparkle icon.
- Sends are batched client-side: consecutive user sends within 60s render immediately in chat but flush to `/api/app/chat` as one combined payload after the window, or immediately if Send/Enter is pressed while the input is empty and a batch is pending.
- Performance: Chat is pre-mounted behind Home; per-bubble `backdrop-filter` removed because Android WebView was visibly stalling when entering Chat.
