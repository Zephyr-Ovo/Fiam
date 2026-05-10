# Fiam Browser Bridge

Local Edge/Firefox extension for sending compact page snapshots to Fiam.

## Install for development

1. Firefox: open `about:debugging#/runtime/this-firefox`, click "Load Temporary
  Add-on", and select `manifest.json`.
2. Edge/Chrome: copy `manifest.chromium.json` to `manifest.json` in a throwaway
  working copy, then load the directory as an unpacked extension.
3. Open the extension options.
4. Set endpoint, usually `http://127.0.0.1:8766`.
5. Set `FIAM_INGEST_TOKEN` from the local environment.

The default checked-in manifest is Firefox-friendly because Firefox currently
rejects MV3 service workers in temporary add-ons on this machine.

## Endpoints

- `POST /browser/snapshot` records a compact snapshot through
  `fiam/receive/browser`.
- `POST /browser/ask` sends the compact snapshot plus a question to `runtime=api`
  or `runtime=cc` with `source=browser`.
- `POST /browser/tick` asks AI for the next autonomous browser decision. Replies
  may include one hidden `<browser_action ... />` or a hidden
  `<browser_done reason="..." />` stop marker.
- `POST /browser/action-result` records an executed browser action and the
  post-action snapshot into flow.

## Autonomous session

Use **Start autonomous session** from the popup to duplicate the current http(s)
page into a controlled browser window. The background script then runs a loop:
snapshot -> `/browser/tick` -> execute at most one returned action ->
`/browser/action-result` -> next snapshot/tick. It stops on `browser_done`, no
action, repeated no-change action, or the configured max step count.

The options checkbox for page-change takeover is intentionally off by default;
explicit sessions and debug ticks still run autonomously without per-action
approval.