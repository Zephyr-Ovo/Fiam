# Atrium — Desktop host for ai

> Latin *atrium* = central hall / entry court.
> Pairs with [`limen`](../limen/) (threshold) on the wearable side.

Atrium is the high-privilege desktop client that gives ai **hands and eyes** on
Iris's PC. The brain stays on ISP; atrium executes a curated set of sensors and
actuators on the daemon's behalf, gated by [`capabilities.toml`](./capabilities.toml),
trust levels, and an append-only `audit.jsonl`.

## Why this exists

Fiam already has eyes (favilla, limen, email) and a brain (daemon + conductor + pool).
What is missing is a body on the desktop: the ability to *intercept* a wandering
browser tab, *open* a reference page during teaching, *spawn* a chess board, *type*
into a shared editor — all decided by ai based on the relationship and the moment,
not by hard-coded rules.

> "AI 伴学室" / "知识类文游" / "共读" / "共创" / "下棋" / "教学" are all upper-layer
> scripts run by the daemon; atrium only exposes primitives.

## Architecture

```
   sensors (passive)              actuators (active)
┌──────────────────────┐    ┌──────────────────────────────┐
│ aw.window.subscribe  │    │ web.intercept   (mitmproxy)  │
│ aw.afk.subscribe     │    │ web.open / web.screenshot    │
│ aw.web.subscribe     │    │ process.list / process.kill  │
│ screen.region        │    │ overlay.dialog (Tauri window)│
│ fs.watch             │    │ input.type / input.click     │
└──────────┬───────────┘    │ fs.read / fs.write           │
           │                │ app.spawn (board / reader)   │
           │                └──────────────┬───────────────┘
           │   capabilities.toml (trust 0/1/2/3)
           └──────────────► audit.jsonl ◄──┘
                            │
                  MQTT ⇄ tailnet ⇄ ISP daemon
```

## Trust levels

Capabilities are tagged in `capabilities.toml`. Atrium does not decide *whether*
to act — that is the daemon's call — but it **does** decide *how to confirm*.

| trust | behavior                                            | examples                                  |
|-------|-----------------------------------------------------|-------------------------------------------|
| 0     | execute silently                                    | `aw.query`, `web.screenshot`, `notify`    |
| 1     | execute, surface a non-blocking toast               | `web.open`, `app.spawn`, `fs.read`        |
| 2     | block until user clicks confirm/deny                | `process.kill`, `fs.write`, `window.uia.*` |
| 3     | always require confirm + reason + audit highlight   | `web.intercept.*`, `mitm.toggle`, `input.borrow_focus` |

## Tech stack

- **Tauri** (Rust core + Svelte 5 frontend, reusing dashboard components)
- **MQTT** client → ISP `mosquitto` over tailnet, topics `fiam/dispatch/desktop/+`
  and `fiam/receive/desktop/+`
- **mitmproxy** as a managed subprocess for web interception (M1 actuator)
- **ActivityWatch** at `localhost:5600` as the M1 sensor (no extra watchers needed)

## Design decisions (2026-05-08)

- Atrium is not a single-purpose study-room app. It is a local capability layer:
  closer to a small desktop operating layer than a normal application.
- First implementation stays a **single Tauri process**: tray, UI, MQTT client,
  audit log, mitmproxy manager, and capability execution all live together.
  A Windows service or elevated helper can be split out later.
- Permission model stays at **trust 0/1/2/3** for now. No generalized lease system
  in M1. Temporary allow windows may exist inside the web-intercept feature, but
  they are not a universal permission primitive yet.
- Web interception starts with **mitmproxy**, not a PAC-only Rust proxy. Atrium
  owns starting/stopping mitmproxy, proxy settings, certificates, rules, and the
  user-facing overlay.
- ActivityWatch is only one sensor. Atrium should read it when available, but it
  must not depend on AW for core decisions or startup.

## Input Model

The desktop goal is: **ai can do its work while Iris keeps using the PC**.
Visible multi-cursor UI is optional; non-interference matters more than showing
a second pointer.

Windows does not provide a universal "second real mouse controls arbitrary apps
without focus" primitive. Atrium therefore uses target-specific operation lanes:

1. **AI-owned surfaces**: Atrium windows, co-reader, chess board, document viewer,
  and any browser/page Atrium opens for ai. ai can freely click, scroll, and
   type there without affecting Iris's active window.
2. **Browser/CDP lane**: when Atrium controls a browser surface, it should use
   DevTools-style page actions instead of moving the system cursor.
3. **UI Automation lane**: for native desktop controls that expose accessibility
   patterns, invoke buttons, select items, and set text without stealing focus.
4. **Win32 control lane**: for classic windows, try ControlClick-style message
   dispatch (`PostMessage`/`SendMessage`) where it is reliable.
5. **Real input fallback**: only when the above lanes cannot work, Atrium may ask
   to borrow the real cursor/focus briefly and use system input. This is a high
   trust action and should be noisy in the audit log.

MouseMux or similar tools are useful references for co-presence UI, but they are
not a core dependency. If true multi-device raw input becomes necessary later,
it should be researched as a separate driver/adapter, not baked into M1.

## M1 scope

Smallest end-to-end loop that *feels* like the goal:

1. Tauri shell, system tray, audit-log viewer.
2. Rust MQTT client wired to ISP daemon.
3. Capability registry + trust gate + append-only audit log.
4. Managed mitmproxy subprocess with rule add/remove/toggle.
5. User-facing overlay/dialog when traffic is intercepted or a trust gate blocks.
6. Daemon-side script: hit → dispatch dialogue → user reply → daemon decides
    release / continue / lockdown.

Result: typing `bilibili.com` in the browser is intercepted; ai actually
talks to you on the page; answering well grants you N minutes.

## Implementation Plan

### M0 — Tauri shell and local core

- Done: Tauri + Svelte + TypeScript skeleton under `channels/atrium/`.
- Done: single Rust process with tray menu, local UI commands, capability registry,
  audit log, and pause/panic state.
- Done: `capabilities.toml` loads at startup; unknown/disabled capabilities are
  rejected by `dry_run_dispatch` and recorded in `audit.jsonl`.
- Done: panic/pause switch blocks actuators immediately.

Local validation:

```powershell
npm --prefix channels/atrium run build
cargo check --manifest-path channels/atrium/src-tauri/Cargo.toml
python -m pip install -r channels/atrium/requirements.txt
```

On this Windows machine, Cargo may need a VS/Windows SDK developer environment
or equivalent temporary `LIB`/`INCLUDE` paths. The global Git proxy also points
at `127.0.0.1:10809`; use `cargo --config "http.proxy=''" ...` if that proxy is
not running.

### M1 — Capability bus and audit

- Subscribe to `fiam/dispatch/desktop` and `fiam/dispatch/desktop/+` over MQTT.
- Publish capability results to `fiam/receive/desktop/result`.
- Read broker defaults from the root `fiam.toml` `[mqtt]` section.
- Surface MQTT connection state and traffic counts in the Atrium UI.
- Trust 2/3 dispatches enter a local pending-confirmation queue; approve/deny
  actions are audited and final results are published back to MQTT when possible.
- Minimal dispatch payload:

```json
{
  "id": "uuid",
  "capability": "web.intercept.add",
  "reason": "brief human-readable reason",
  "payload": {}
}
```

- Minimal result payload:

```json
{
  "id": "same uuid",
  "ok": true,
  "result": {},
  "audit_id": "..."
}
```

- Audit every request, denial, confirmation, subprocess start/stop, and failure.
- M1 execution is capability-gated and audit-first. Capability-specific side
  effects land in the later feature milestones; disabled capabilities remain
  hard-denied.

### M2 — mitmproxy interception

- Atrium starts/stops `mitmdump` as a managed child process on local port `8088`.
- The `mitmdump` path resolves from `ATRIUM_MITMDUMP`, then the workspace
  `.venv/Scripts/mitmdump.exe`, then PATH.
- Python runtime dependency is pinned in `requirements.txt`; validated here with
  mitmproxy `12.2.2`.
- Atrium owns `tools/mitmproxy/rules.json` and exposes
  `web.intercept.add/remove/release` plus `mitm.toggle` through the capability bus.
- `tools/mitmproxy/rules.py` reloads the JSON rules on each request and returns a
  local `451` page for matching host/path patterns.
- Atrium owns proxy/certificate setup and recovery UX.
- Intercept hits open an Atrium overlay/dialog rather than relying only on the
  browser's error page.
- Trust stays simple: rule changes and proxy toggles are trust 3.

### M3 — Trust gates and recovery UX

- High-trust actions (`web.intercept.*`, `mitm.toggle`, future real input) must
  enter the Trust Gate panel before execution.
- Approving a pending dispatch executes the original request and publishes the
  final result back to `fiam/receive/desktop/result` when MQTT is connected.
- Denying a pending dispatch records `dispatch.denied` in `audit.jsonl` and also
  returns a denial result over MQTT when possible.
- Proxy/certificate recovery UX and the intercepted-page overlay still need the
  next UI pass; M3 starts with the permission backbone.

### M4 — Proxy recovery UX

- Atrium reads Windows user proxy settings from
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`.
- `proxy.system.apply` saves the current proxy snapshot to
  `proxy-snapshot.json`, applies `127.0.0.1:8088`, and asks WinINet to refresh.
- `proxy.system.restore` restores the saved snapshot and refreshes WinINet.
- Both proxy capabilities are trust 3 and therefore go through Trust Gate before
  registry writes happen.
- Atrium also shows the mitmproxy CA certificate path and can open the `.cer`
  file after Trust Gate confirmation; Windows certificate installation remains
  a visible OS-controlled action.

### M5 — Intercept inbox

- `rules.py` writes each blocked request to `tools/mitmproxy/intercepts.jsonl`.
- Atrium reads that inbox into an Intercepts panel with host/path/rule context.
- The Release action dispatches `web.intercept.release` for the matched rule and
  therefore still requires Trust Gate approval before the rule is relaxed.
- The browser still receives a local `451` page; the desktop panel now gives the
  user a live place to inspect and respond.

### M6 — Release dialog

- Selecting an intercept hit opens a local Release Dialog panel.
- The user writes the release reason/reply and chooses a release window in
  minutes.
- Release dispatches `web.intercept.release` with `{id, minutes}` and the typed
  reason; execution still waits for Trust Gate approval.

### M7 — Read-only desktop inventory

- `window.list` uses Win32 `EnumWindows` to list visible titled windows, pid,
  hwnd, and focus state without changing focus.
- `process.list` uses a ToolHelp process snapshot to list running processes.
- Atrium exposes both through capability dispatch and a local Inventory panel.

### M8 — Atrium-owned reader surface

- `app.spawn` can open an Atrium-owned reader/co-reader window.
- The reader surface is a separate Tauri webview using `?surface=reader`; it is
  editable and isolated from Iris's current active application.
- This is the first non-focus-first operation lane: future ai actions should
  target Atrium-owned surfaces before touching arbitrary desktop windows.

### M9 — Reader surface actions

- `reader.set_text` and `reader.append_text` send Tauri events to the latest
  Atrium-owned reader surface.
- These actions update the reader window without moving the system cursor,
  stealing focus, or using keyboard input.

### M10 — Non-interfering operation lanes

- `web.surface.open`, `web.surface.navigate`, and `web.surface.reload` manage an
  Atrium-owned WebView2 browser surface instead of Iris's default browser.
- `web.cdp.click`, `web.cdp.type`, and `web.cdp.scroll` execute page actions
  inside that owned surface without moving the system cursor or using keyboard
  input.
- Window/process inventory remains the routing sensor for deciding whether a
  request should stay in an owned surface or escalate to another lane.

### M11 — Native control lanes

- `window.win32.control_click` posts mouse messages to a target `hwnd` without
  moving the real cursor or borrowing focus.
- `window.win32.set_text` sends `WM_SETTEXT` to a target `hwnd` without keyboard
  input.
- Both actions are trust 2 and must pass the local Trust Gate before execution.
- UI Automation remains the next native lane because it needs control-tree and
  pattern detection rather than raw window handles.

### M12 — Browser extension bridge

- `channels/atrium/browser-extension` is an unpacked Edge/Firefox development
  extension that collects compact page snapshots from the active tab.
- `POST /browser/snapshot` records a compact page context through
  `fiam/receive/browser`; the browser plugin is `auto_wake = false`, so passive
  snapshots enter flow without waking ai by default.
- `POST /browser/ask` sends the compact snapshot plus a user question to either
  `runtime=api` or `runtime=cc` with `source=browser`, so both runtimes record
  the turn under `user@browser` / `ai@browser`.
- `src/fiam/browser_bridge.py` owns snapshot normalization, context limits, and
  runtime prompt formatting; browser-specific selectors stay outside core flow
  and runtime code.

### M13 — UI Automation lanes

- Add UI Automation actions for accessible native controls.
- Keep real `SendInput` as a confirmed fallback only.

### M14 — Sensors

- Use ActivityWatch as one optional source if `localhost:5600` is available.
- Fall back to Win32 active-window/process sensors when AW is absent.
- Later: screen region capture, OCR, filesystem watch, audio/TTS.

### M15 — OS-like UI

- Build the first page as a control surface, not a marketing/dashboard page:
  capabilities, current gates, active tasks, audit, proxy state, and overlay tests.
- Visual language should rhyme with Favilla, but with desktop density and stronger
  operator affordances.
- Knowledge-game/study-room/co-creation surfaces grow on top after the local core
  is reliable.

### M16 — Higher privilege adapters

- Split an elevated helper only when required.
- Candidate future adapters: WFP/WinDivert, firewall-level blocking, service mode,
  virtual HID/raw-input experiments, WSL sidecars, local model/tool runners.

## Layout (planned)

```
channels/atrium/
  capabilities.toml      ← capability registry + trust levels
  package.json           ← Svelte/Tauri frontend scripts
  src-tauri/             ← Rust backend (tray, capability registry, audit)
  src/                   ← Svelte frontend (control surface)
  tools/
    mitmproxy/rules.py   ← interception script
    mitmproxy/rules.json ← current intercept ruleset
  README.md
```

## Status

M0 local core is implemented. [`plugins/atrium/plugin.toml`](../../plugins/atrium/plugin.toml)
is still `enabled = false`; M1 will add MQTT dispatch and live capability execution.
Atrium replaces the older `device-control` placeholder plugin.
