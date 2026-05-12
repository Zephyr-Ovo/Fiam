# Fiam Studio Obsidian Plugin Devlog

## 2026-05-12

- Scope: plugin source lives under `F:\fiam-code\channels\obsidian-fiam-studio`; `D:\DevTools\lib\studio` is the Studio vault/content repo, not a code repo.
- Product direction: Obsidian is the primary Studio surface. Do not continue the old TipTap editor direction.
- MVP target: Inbox/Desk/Shelf panels, Quick note, selection capture, AI co-author block insertion, git commit/sync, and git-log timeline.
- User requirement: do not add human-approval or permission restrictions for AI. AI-authored operations should be treated as first-class authored writes, not gated actions.
- Implemented vanilla Obsidian plugin MVP with no React/TipTap dependency. Commands: open panel, quick note, selection to inbox, append AI co-author block, git sync. View tabs: Timeline, Inbox, Desk, Shelf, Quick, Co-create.
- Build command: `F:\node.exe F:\node_modules\npm\bin\npm-cli.js --prefix F:\fiam-code\channels\obsidian-fiam-studio run build`.
- Install command: `$env:FIAM_STUDIO_VAULT_DIR='D:\DevTools\lib\studio'; F:\node.exe F:\node_modules\npm\bin\npm-cli.js --prefix F:\fiam-code\channels\obsidian-fiam-studio run install:studio`.
- Installed plugin artifact to `D:\DevTools\lib\studio\.obsidian\plugins\fiam-studio`; this is ignored by the vault repo.
