# Studio 设计

> 顶部 = 当前方案，可批注/修改。底部 = 交流历史，按时间累加。
> 受众: zephyr / cc / copilot / codex 共看。回复请在底部追加段落。

---

## 当前方案 v0.1 (2026-05-11 整合 Codex + Zephyr 反馈)

### 0. 一句话

Studio = **vault (markdown 仓库) + 收件接口 + 时间线阅读器 + Obsidian 跳转**。
编辑交给 Obsidian / VSCode / Logseq；Studio 不再追求富文本编辑器。

【Codex 批注】同意。这里的重点不是“依赖 Obsidian”，而是把 Studio 从“编辑器产品”改成“个人文本物流/阅读入口”。Obsidian 只是第一优先编辑器，VSCode/Logseq/纯文件系统都可以替代。

### 1. vault 物理位置 + git

| 路径                                | 角色                                      |
|-------------------------------------|-------------------------------------------|
| `/home/fiet/live/studio/`           | vault 根 (**独立 git repo**, 非 submodule) |
| `/home/fiet/live/studio/shelf/`     | **书架**: 长文、网页存档、阅读材料、未来 epub/pdf 索引 |
| `/home/fiet/live/studio/desk/`      | **书桌**: 创作仓库、草稿、项目笔记、Quick Note |
| `/home/fiet/live/studio_ai_inbox/`  | **AI 私有收件箱**: 外部 share / 临时批注 / 待整理材料；不在用户本地 vault 中 |

【Zephyr 批注】不强求写什么，可以不写日记，一个书架，一个邮筒，一个书桌，书桌就是创作仓库，怎么建立文件夹没有规定。

【Codex 批注】采纳。v0.1 不应规定“日记”作为核心概念，`daily/` 改为 `desk/` 更稳。`shelf/desk` 是用户本地 vault 的默认隐喻，不是强制分类法；用户可以在 vault 里自由建文件夹。`inbox` 是 AI 私有收件箱，不在用户本地 vault 中。

【Zephyr 问题】关于书架，也就是阅读器，你们有什么想法？

【Codex 回答】书架不该只是一个文件夹，而是 Reader 的数据来源集合。v0.1 先支持 markdown / 网页存档：`shelf/<source>/<slug>.md`，每篇一文件，有 frontmatter。Reader 做三件事即可：只读渲染、选中文本写批注到 AI 私有 inbox 或用户 desk、跳 Obsidian。epub/pdf/image archive 等先只登记 metadata，不急着解析全文。

- vault 是**独立 git repo**，不进 fiam-code 历史。
- 远端 = **GitHub 私 repo** (Zephyr 待新建)。Obsidian 桌面/移动用 **Obsidian Git 插件**双向同步。Studio app 服务器侧也走同一 repo。
- 后端写文件后 `git add + commit`；timeline 即 `git log`。
- 本地开发用 env `FIAM_STUDIO_VAULT_DIR` 覆盖默认路径。

### 2. 文件内容格式 (Codex 提议, 采纳)

**多条 append 类文件** (`studio_ai_inbox/YYYY-MM-DD.md`, `desk/YYYY-MM-DD.md`) — 每条用 markdown block:

```md
## 12:34 · atrium · copilot
> selected text

note...

source: https://...
tags: #studio/inbox
```

**单页归档** (`shelf/<source>/<id>.md`) — 文件级 YAML frontmatter:

```md
---
source: atrium
url: https://...
ts: 2026-05-11T12:34:56Z
agent: copilot
tags: [web, ...]
---

(正文)
```

### 3. 后端 API (dashboard_server.py 新增)

所有 `/studio/*` **写接口** 走 `X-Fiam-Token` (FIAM_INGEST_TOKEN)。
**读接口** 暂时也走同一 token (后续再分 viewer token)。

#### POST `/studio/share`
```json
{
  "source": "atrium" | "favilla" | "quicknote" | "manual" | "obsidian",
  "url": "<可选>",
  "selection": "<选中/正文>",
  "target_file": "<可选, hint>",
  "note": "<可选>",
  "agent": "zephyr|cc|copilot|codex"
}
```

后端规则:
- `target_file` 若缺省，按 source 决定:
  - `quicknote` → `desk/YYYY-MM-DD.md`
  - `atrium` / `favilla` / `manual` / `obsidian` → AI 私有 `studio_ai_inbox/YYYY-MM-DD.md`
- 若 `target_file` 指向 `desk/` 或其他用户 vault 路径 → append 一个上述 markdown block
- 若指向 `shelf/...` → 创建新文件，写 YAML frontmatter + 正文
- 显式 `target_file=inbox/...` 拒绝，避免重新把 AI 私有收件箱写回用户 vault。
- **path traversal 防护**: 用户 vault 与 AI 私有 inbox 都必须阻止越界。
- 自动 `mkdir -p` 父目录，UTF-8 写入，git commit。commit 失败 → 返回明确错误，不静默。
- commit 作者 = 服务器用户；真实来源在 block 内的 `agent` + commit message 体现。
- 返回 `{ok, commit_sha, abs_path, rel_path}`

#### POST `/studio/quicknote`
```json
{ "text": "...", "agent": "zephyr" }
```
等价于 `share` with `source=quicknote, selection=text, target_file=desk/<today>.md`。
快捷便利封装。

#### GET `/studio/list?dir=desk&limit=50`
返回:
```json
{
  "files": [{path, mtime, size, last_commit_sha, last_commit_ts}],
  "log":   [{sha, msg, ts, files}]
}
```
**默认按 git commit ts 排序** (mtime 不可靠)。

#### GET `/studio/file?path=desk/2026-05-11.md`
返回 raw markdown (text/plain)。前端自渲染。
路径必须在 vault 内。

### 4. 前端 (channels/favilla/app)

【Zephyr 批注】全功能在桌面端，手机端塞不下，做轻量的，小功能。

【Codex 批注】采纳。手机端只做“收、看、跳、快记”：Reader 简版、Quick Note、分享入口、跳 Obsidian。桌面端/Atrium 才承载文件管理、批量整理、长文阅读、git 状态、Obsidian 跳转细节。Inbox 是 AI 私有入口，不做用户本地页面。

新增 `/studio` 路由。原 `EditorView` 从路由摘掉，代码留 archive。

```
/studio
  ├─ Timeline  (默认)  — git log 时间线
  ├─ Reader    /studio/r?path=...  (react-markdown + remark-gfm 只读)
  ├─ Quick     单 textarea, enter → /studio/quicknote
  └─ Settings  vault 路径 / Obsidian URI scheme / git remote 状态
```

### 5. Obsidian 跳转

桌面/移动 Obsidian 装好 vault 后注册 URI scheme:
```
obsidian://open?vault=fiam-studio&file=<relpath_no_ext>
```
Reader 右上角按钮即跳。
**待验**: Capacitor WebView 是否能弹出 `obsidian://` Android intent。第二步再处理。

### 6. 集成点

| 来源              | 入口                                  |
|-------------------|---------------------------------------|
| Atrium 浏览器扩展 | popup "送到 Studio" → `/studio/share` (source=atrium) |
| Favilla 聊天分享  | 长按消息 → `/studio/share` (source=favilla) |
| Quick Note        | Studio app /quick → `/studio/quicknote` |
| 邮件 (未来)       | bridge_email 抓附件 → `/studio/share` |

注意: **`/api/capture` 不并入 `/studio/share`**。capture 是 fiam channel ingestion (走 flow / pool / spread)，share 是 vault 写入。两条路可以同一个动作触发，但接口分离。

### 7. 落地顺序 (v0.1)

1. **Zephyr 新建 GitHub 私 repo** (vault 用)，给 ssh / https URL。
2. ISP 上 `mkdir /home/fiet/live/studio && cd ... && git init && git remote add origin <repo>` + 写 `README.md` + 初始 commit + push。
3. dashboard_server.py 加 4 个 `/studio/*` 接口 + path-traversal 防护。
4. (脚本) `scripts/studio_init.py` 或 vault init 步骤写进 README。
5. 跑通后再做前端 ReaderView / Timeline / Quick。
6. 再做 Atrium 扩展 popup 按钮 + Favilla 长按分享。
7. (可选) Android Obsidian URI 跳转 smoke。

【Codex 批注】顺序建议保持“后端先行”。只要 `/studio/share` 能写 vault + commit，后面的手机/桌面 UI 都能围绕真实数据迭代，避免先画空壳。

### 7.5. 已落地进度 (2026-05-11 copilot, commit 0ca02f7, 本地未推)

- ✅ 后端 4 个 `/studio/*` endpoint (scripts/dashboard_server.py)
  - POST `/studio/share` — 默认写 AI 私有 inbox；指定 desk/其他 vault 路径则 append block；指定 shelf 则写 frontmatter
  - POST `/studio/quicknote` — 等价 share + source=quicknote → desk/今天.md
  - GET  `/studio/list?dir=&limit=` — 文件列表 + git log（按 mtime 排）
  - GET  `/studio/file?path=` — 返回 raw markdown
- ✅ Path-traversal 防护、`.git` 拒绝、`.md` 强制
- ✅ vault 路径用 env `FIAM_STUDIO_VAULT_DIR` 覆盖（本地/测试），默认 `<home>/studio`
- ✅ 自动 `git init` + 默认 user.name/email；每次写自动 `git add + commit`（best-effort，失败不抛错）
- ✅ AI 私有 inbox/desk = 多条 markdown block append；shelf = YAML frontmatter，**拒绝覆盖**
- ✅ tests/test_studio.py — 12 个用例，全过；主测试套件 77 通过无回归
- ✅ ISP AI smoke: AI 在 ISP 上以 `/home/fiet/live/studio_ai_inbox/` 作为自己的 inbox；通过 `/studio/share` 默认写该 inbox，通过 `/studio/quicknote` 写用户 vault 的 `desk/`，再用 `/studio/list` + `/studio/file` 读回用户 vault 内容；显式 `target_file=inbox/...` 返回 400，避免把 AI inbox 写回用户 vault。
- ✅ Atrium 扩展 popup 「送到 Studio」按钮（inbox / desk / shelf 三选一 + note 备注）
  - content.js 新增 `FIAM_GET_SELECTION` 消息，自动抓 url + title + 选中文本
  - popup.js 走 `/studio/share` 带 `source=atrium`
  - xpi 已重新打包到 build/atrium-browser-extension.xpi

待办：
- [ ] 前端 dashboard 加 `/studio` 路由（Timeline + Reader 只读 + Quick）—— 先观察 vault 实际数据形态再做
- [ ] Favilla app 长按消息 → /studio/share —— 需要先确认 Favilla 端 long-press UX
- [ ] Obsidian Android URI 跳转 smoke
- [ ] 部署到 ISP（**用户出门期间 CC 可能在改 ISP 上的东西，等用户回来再决定**）

### 8. 待 Zephyr 提供 / 决策

- [ ] **新 GitHub repo URL** (vault 用，Zephyr 个人或与 AI 共享的 org 都行)
- [ ] vault repo 是否需要 LFS？(若以后存图片/PDF；v0.1 可先不启用，先只存 markdown + 外链)
- [ ] Obsidian vault 名 = `fiam-studio` 行不行？影响 URI scheme 字符串
- [x] `inbox` 改为 AI 私有，不进入用户本地 vault；用户本地默认只保留 `shelf/desk`，并可自由新增文件夹。

---

## 群聊 / 信使模式 (2026-05-11 决议, 不属于 Studio)

不搭 dev-room daemon, 不让 AI 自己 poll。改用人肉信使:
- copilot ↔ cc: 通过 Favilla app 发消息, 正文注明 "from copilot to cc"。
- copilot ↔ codex: copilot 写 md 到 fiam-code 根目录, zephyr 转给 codex; codex 回复也写在同目录。
- cc ↔ codex: 同上, cc 写 md, zephyr 转。

最小开销, 没有新 infra。

---

## 讨论历史 (按时间, 顶部内容已整合可不读)

### 2026-05-11 codex 初版方向 (已采纳, 见顶部)
拆 Studio = Inbox + Timeline + Reader + Obsidian 跳转; 不做完整富文本编辑器。

### 2026-05-11 copilot 整理 v0 + 7 个开放问题 (已被 Codex 回复 + 本次整合替代)

### 2026-05-11 codex 回复 7 个开放问题 (要点已并入顶部 v0.1)
- vault 独立 git repo, 非 submodule。`FIAM_STUDIO_VAULT_DIR` env 覆盖。
- target_file 后端 normalize + path traversal 防护, 缺省按 source 决定。
- 多条 append 用 markdown block, 单页归档用 YAML frontmatter。
- Inbox 按 git commit ts。
- Quick Note → daily/。
- /api/capture 与 /studio/share 分离。
- sync 不进 v0.1。
- 写接口走 FIAM_INGEST_TOKEN; mkdir + UTF-8 append + commit 失败要明确报错。
- 下一步: 后端 4 接口 + vault 初始化先行, 再动 UI。

### 2026-05-11 01:42 zephyr 补充
同步走 git via Obsidian Git 插件 (有现成 repo / 工具)。需要新建 GitHub vault repo 时告诉 Zephyr。

### 2026-05-11 copilot 整合
顶部 v0.1 即整合结果。等 Zephyr 给 vault repo URL 启动落地。

### 2026-05-11 codex 批注顶部方案
采纳 Zephyr 对“不要强制日记/固定文件夹”的反馈，把默认目录隐喻改成 `inbox/` 邮筒、`shelf/` 书架、`desk/` 书桌。Reader 先作为书架内容的只读入口，手机端只做轻量收/看/跳/快记，桌面端承载完整整理能力。

— Codex

> 后续讨论 / 反对意见请在此追加段落。
