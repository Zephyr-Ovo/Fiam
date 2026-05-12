# Fiam Studio Obsidian Plugin

Fiam Studio lives in the Obsidian vault. The plugin provides workflow panels and commands for:

- `inbox/`: captures from browser, phone, email, and manual notes.
- `desk/`: drafts, notebooks, and active co-authoring.
- `shelf/`: reading material and archive notes.
- Git timeline: commit history as the time axis.
- Co-authoring: human and AI edits are first-class authored blocks and commits.

The Studio vault is content. Plugin source lives here, outside the vault.

## Develop

```powershell
npm install
npm run build
```

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
