# Fiam Integration Notes — External Repo Research

> 2026-05-15. Research on repos Zephyr shared + epub reader landscape.
> Goal: music, browser (Bilibili), WeChat, epub reading for Fiam ecosystem.

## TL;DR

| Need | Recommended approach | Source |
|------|---------------------|--------|
| Bilibili | yt-dlp (already battle-tested) | Agent-Reach uses it internally |
| WeChat | Study QwenPaw's channel impl, cautiously | QwenPaw (Apache 2.0) |
| Music | yt-dlp audio extraction + local player | No good API left |
| EPUB | foliate-js in Obsidian plugin | foliate-js (MIT) |

## Repo Verdicts

### Agent-Reach (Panniantong) -- worth borrowing from

19.5k stars, MIT, actively maintained. CLI toolkit giving AI agents access to 15+ platforms: Bilibili, WeChat, YouTube, XiaoHongShu, Reddit, Twitter, RSS, etc.

**What's good:** Clean channel abstraction (`Channel` base class, `can_handle()` + `check()`). Battle-tested yt-dlp integration for video/audio. Bilibili and WeChat support already wired.

**What's not:** The 1,743-line `cli.py` monolith is an installer/doctor/config manager -- it's *their* problem, not ours. We wouldn't use AR as a dependency; we'd study how they call specific tools and replicate the thin parts.

**Fiam angle:** AR's value is that someone has already figured out the auth/API quirks for each platform. For Bilibili specifically, all they do is wrap `yt-dlp` with the right cookies and format selectors. We can do that directly. For WeChat, their approach likely uses the same fragile web/hook methods everyone does -- see QwenPaw below for a cleaner implementation.

**Verdict: Reference, don't depend.** Extract the 20 lines of yt-dlp bilibili config, ignore the rest.

### QwenPaw (agentscope-ai / Alibaba) -- the serious one

16.7k stars, Apache 2.0, multiple commits per day. Full personal AI assistant with 11+ chat platform channels.

**What's good:** Genuinely good engineering. Channel base class pattern almost identical to Fiam's. Clean separation of agents/channels/providers/security. Lazy imports for fast startup. Security layer with YAML rule engine. Tests exist (unit/contract/integration/e2e). WeChat, DingTalk, Feishu, Discord, Telegram, QQ, Matrix, MQTT, iMessage, SIP/voice channels.

**What's not:** Deep Alibaba ecosystem tie-in (AgentScope, ModelScope, DingTalk). The `agentscope` dependency pulls a lot. 152k LOC is a big surface.

**Fiam angle:** This is the most architecturally similar project to Fiam. Their channel abstraction maps cleanly to ours. Worth studying for:
- **WeChat channel implementation** -- how they handle auth, message types, group vs DM, rate limits. WeChat integration is notoriously fragile (web WeChat restricted, hook-based approaches break on updates). Their approach is the best-documented open-source version I've seen.
- **Cron/scheduling patterns** -- APScheduler integration for recurring tasks.
- **Security model** -- tool guard with allowlist/blocklist YAML rules. Fiam could adopt something similar.

**Verdict: Study and selectively port.** The WeChat channel code specifically is worth reading line-by-line. Don't take the framework -- take the protocol knowledge.

### bookwith (shutootaki) -- impressive architecture, can't use

294 stars, AGPL-3.0. AI-powered ebook reader: EPUB upload, chat-with-book, podcast generation, semantic cross-book search.

**What's good:** Backend follows DDD rigorously -- domain entities with value objects, repository interfaces, use-case layer, dependency injection. This is rare for a side project. Frontend is well-componentized (Next.js 15, Radix UI, Jotai/Valtio).

**What's not:** AGPL license is a dealbreaker for an Obsidian plugin. Zero tests despite 177 Python files and 168 TS files. CORS `allow_origins=["*"]`. "tennant_id" typo baked into domain layer. Heavy dependency surface (LangChain + Weaviate + ChromaDB + Google Cloud TTS + Gemini + Dropbox SDK) with no integration tests.

**Fiam angle:** The "chat with book" concept is interesting -- they use LangChain + ChromaDB to chunk epub content and do RAG. If we build epub reading into Studio, this is a natural extension. But we'd implement it ourselves with Fiam's existing runtime (not LangChain). Their forked epub.js is less interesting than foliate-js.

**Verdict: Inspiration only.** The DDD patterns are nice to look at. The actual code can't be used (AGPL) and isn't tested.

### Microsoft UFO -- wrong platform, interesting ideas

8.6k stars, MIT. Windows GUI automation via LLM. Three generations of increasingly ambitious scope (single-app -> desktop AgentOS -> multi-device DAG).

**What's good:** Clean agent architecture (HostAgent/AppAgent with ReAct loops). DAG-based task decomposition in Galaxy. Well-documented.

**What's not:** Windows-only (pywinauto/pyautogui/uiautomation). Research-grade test suite (debug scripts mixed with tests). Exact-pinned dependencies.

**Fiam angle:** Conceptually relevant to Atrium's ambitions (desktop automation, cross-app coordination), but technically incompatible -- Atrium is Tauri/Rust, not Python/pywinauto. The DAG orchestration pattern in Galaxy is intellectually interesting for Fiam's flow/pool system. Not actionable near-term.

**Verdict: File away.** Read the Galaxy paper if you're curious about multi-agent task decomposition. Don't integrate.

### NeteaseCloudMusicApi (Binaryify) -- dead

30k historical stars. Archived April 2024, all source code wiped under copyright pressure from NetEase. Only a 3-line README remains. Community forks exist but are legally grey.

**Verdict: Gone.** Don't touch.

## The Four Features: How I'd Build Them

### 1. Music

The landscape is grim for APIs. NeteaseCloudMusic is dead. Spotify's API requires OAuth and doesn't stream full tracks. QQ Music has no public API.

**Pragmatic path:** yt-dlp can extract audio from YouTube, Bilibili, SoundCloud, and 1000+ sites. For a personal-use tool:

```
yt-dlp --extract-audio --audio-format opus -o "%(title)s.%(ext)s" <url>
```

Integration with Fiam:
- Atrium or a new `music` channel: accepts a URL, yt-dlp extracts audio, stores in a local library.
- Playback: a lightweight web audio player in the Obsidian plugin (HTML5 `<audio>` + a minimal playlist UI), or just open in system player.
- Track integration: listening events logged to `track/music.md` via the existing collector pipeline.
- No streaming service dependency. Your music, your files.

I would NOT build a full music app. A command that downloads + a simple player is enough.

### 2. Browser / Bilibili

Atrium already plans browser integration (M12 milestone: browser extension bridge, web interception).

For Bilibili specifically:
- **Watch/download:** yt-dlp with `--cookies-from-browser` handles auth, downloads, format selection. One command.
- **Browse/search:** Agent-Reach wraps Bilibili's search API. Thin enough to replicate (it's just HTTP calls with the right headers).
- **Track integration:** Video watch events -> `track/media.md`.

I'd resist building a Bilibili client. The 80/20 is: download what you want to watch, track what you watched. Browsing stays in the real browser; Atrium's extension bridge captures intent.

### 3. WeChat

The hardest one. WeChat actively fights third-party integration:
- Web WeChat is restricted/disabled for many accounts.
- Hook-based approaches (injecting into WeChat desktop process) break on every update and are TOS-violating.
- Official WeChat Work (WeCom) API exists but is for enterprise, not personal.

QwenPaw's approach is the best reference. Their WeChat channel likely uses one of:
- **wechaty** (TypeScript, supports multiple puppet backends including web, iPad protocol, Windows hook)
- **itchat** (Python, web WeChat only -- increasingly unreliable)
- A proprietary iPad protocol bridge (the most stable but legally grey)

**My recommendation:** Don't build WeChat integration as a core Fiam feature. Instead:
1. Study QwenPaw's WeChat channel code to understand what's actually feasible today.
2. If you want message forwarding, the most stable approach is WeChat Work (WeCom) bot API -- but that requires a corporate account.
3. For personal use, the least fragile option is a simple forwarding bot on a platform you control (Telegram/Matrix) + manual forwarding from WeChat. Ugly but reliable.
4. If you accept the fragility, wechaty with the padlocal puppet is the best open-source option. But expect it to break every few months.

### 4. EPUB Reading

**Clear winner: foliate-js** (johnfactotum/foliate-js, MIT).

- Pure browser JS, no GTK dependency. Runs in Electron/Obsidian natively.
- Supports EPUB, MOBI, KF8, CBZ, FB2.
- Web component API (`<foliate-view>`).
- CFI-based annotation/highlight primitives.
- API is explicitly unstable -- pin via git submodule.

Implementation plan for Obsidian plugin:
1. Add foliate-js as a git submodule (pinned commit).
2. New plugin view: "Reader" tab alongside Timeline/Desk/Shelf.
3. Open `.epub` files from `shelf/` in a foliate-js renderer.
4. Annotation flow: highlight text -> creates a markdown annotation in `shelf/<book>/annotations.md` with CFI anchor + quoted text + timestamp.
5. Reading time tracked -> feeds into `track/reading.md` via existing collector pipeline.
6. Prior art: study obsidian-annotator plugin (already does EPUB + highlight-to-markdown in Obsidian).

This aligns perfectly with STUDIO_ROADMAP P6. foliate-js is the rendering engine; the annotation-to-vault pipeline is the Fiam-specific value-add.

## Priority Order

```
EPUB reader (P6)  >  Bilibili/yt-dlp  >  Music player  >  WeChat
```

EPUB is highest value: it extends the Studio vault naturally, reuses the track pipeline, and has a clean technical path. Bilibili/music are simple yt-dlp wrappers. WeChat is a maintenance liability.

## Code I'd Actually Read

- QwenPaw `src/app/channels/wechat/` -- WeChat protocol specifics
- QwenPaw `src/security/` -- tool guard pattern for Fiam
- Agent-Reach `channels/bilibili.py` -- yt-dlp invocation details
- foliate-js `view.js` + `overlayer.js` -- rendering + annotation API
- obsidian-annotator `src/` -- how they embed epub.js in Obsidian
