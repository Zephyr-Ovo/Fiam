# Fiam Studio Obsidian Plugin

Fiam Studio lives in the Obsidian vault. The plugin provides workflow panels and commands for:

- `desk/`: drafts, notebooks, and active co-authoring.
- `shelf/`: reading material and archive notes.
- Private AI inbox: captures from browser, phone, email, AI agents, and manual notes go through `/studio/share` and do not create a local `inbox/` folder in the vault.
- Git timeline: commit history as the time axis.
- Co-authoring: human and AI edits are first-class authored blocks and commits.

The Studio vault is content. Plugin source lives here, outside the vault.

## Develop

```powershell
npm install
npm run build
```

The private AI inbox command needs the Studio server base URL and `FIAM_INGEST_TOKEN` in the plugin settings.

## ISP AI Usage Smoke

The ISP AI uses Studio through the dashboard API plus the two git-backed directories on the ISP:

- User vault: `/home/fiet/live/studio/`
- AI inbox: `/home/fiet/live/studio_ai_inbox/`

Smoke path:

1. POST `/studio/share` with no `target_file`; it writes to the AI inbox and returns `private: true` with `rel_path` under `ai-inbox/`.
2. Inspect `/home/fiet/live/studio_ai_inbox/YYYY-MM-DD.md`; it should contain the shared block.
3. POST `/studio/quicknote`; it appends to `desk/YYYY-MM-DD.md` in the user vault.
4. GET `/studio/list?dir=desk`; it lists the desk note and recent git log entries.
5. GET `/studio/file?path=<quicknote rel_path>`; it returns raw markdown containing the quick note.
6. POST `/studio/share` with `target_file: "inbox/test.md"`; it returns HTTP 400. The user vault keeps `desk/` and `shelf/`; the AI inbox is the separate ISP repo above.

All `/studio/*` requests require `X-Fiam-Token: <FIAM_INGEST_TOKEN>`.

## Install Into Local Studio Vault

By default the install script targets `D:\DevTools\lib\studio`.

```powershell
npm run install:studio
```

Override:

```powershell
$env:FIAM_STUDIO_VAULT_DIR="D:\DevTools\lib\studio"
npm run install:studio
```
