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
