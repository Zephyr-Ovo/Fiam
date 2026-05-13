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
- Copilot audit: build still passes and TypeScript reports no errors for `src/main.ts`. Current plugin is a usable MVP, not the target Reader/Library product yet: no arbitrary file/folder management UI, no attachment workflow, no webpage preview, no EPUB import/reader, and no annotation model beyond appending an AI-authored block to the active note.
- Private inbox pass: local Obsidian vault no longer has an Inbox panel or `inboxDir` setting. Selection capture posts to the Studio server `/studio/share` with `source=obsidian`; the dashboard backend routes default shares into a separate AI-private inbox repo (`FIAM_STUDIO_AI_INBOX_DIR` or `<home>/studio_ai_inbox`) instead of the content vault. Explicit `inbox/...` targets are rejected.
- Timeline pass: plugin timeline now follows the standalone HTML reference with a center line, left-side human events, right-side AI events, and Favilla Chat Streamline action SVG icons via CSS masks. Node glyphs are action-first rather than author-avatar icons, so AI events do not use a generic sparkle fallback; co-authoring maps to writing, while chat-message is reserved for actual conversation/reply events. The node structure now mirrors the standalone HTML: 2px axis, 20px paper-backed node, 14px optical icon area, no visible badge. Visible event text is title + time only; sha/files move to tooltip details to keep the rail compact. CSS keeps using existing Obsidian/Favilla theme variables instead of replacing the vault color/font system.
- Testing correction: ISP AI is a real Studio user. Instructions for it should state the ISP paths, API calls, inbox/vault ownership, and verification steps: `/studio/share`, `/studio/quicknote`, `/studio/list`, `/studio/file`, `/home/fiet/live/studio_ai_inbox/`, `/home/fiet/live/studio/`, and git state. Local smoke results are Copilot's validation for Zephyr, not the ISP AI's operating context.
