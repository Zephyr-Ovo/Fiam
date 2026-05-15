# Studio Conventions

> 权威约定。后面所有 Studio 相关代码（plugin、记录官、share 接口、reader）按此实现。
> 改约定 = 改本文件 + 通知所有 surface，不要分叉。
>
> 配套：`STUDIO_DESIGN.md`（设计起源）、`STUDIO_ROADMAP.md`（推进顺序）、`plugins/studio/plugin.toml`（fiam 接入）。

## 1. 目录层级

vault 根 = `<home>/studio/`（独立 git repo）。AI 私有 inbox = `<home>/studio_ai_inbox/`（独立 repo，不在 vault 内）。

| 路径                  | 角色                                                   | 谁写                       |
|----------------------|--------------------------------------------------------|----------------------------|
| `desk/`              | 书桌——草稿、Quick Note、项目笔记                       | 人 / AI 都可写              |
| `shelf/`             | 书架——长文、网页归档、阅读材料                         | 人 / AI 都可写              |
| `track/`             | 记录官产出——分层时间线总结（编辑/工作/全系统/阅读…）    | 仅记录官（人/AI 不直接编辑）|
| `studio_ai_inbox/`*  | AI 私有收件箱（**独立 repo，非 vault 子目录**）        | AI / share 默认目标         |

`*` 通过 share 接口写入；显式 `target_file=inbox/...` 拒绝。

不规定其它子目录——用户可在 `desk/` `shelf/` 下自由建文件夹。

## 2. 文件命名

- 全部小写 + 连字符。中文允许。
- append 类（按天累加）：`<dir>/YYYY-MM-DD.md`，每条用段落 block。
- 单页归档：`shelf/<source>/<slug>.md`，文件级 frontmatter。
- 记录官产出：`track/<name>.md`（`name` ∈ {`edit`, `work`, `system`, `reading`, ...}）。

## 3. 文件级 frontmatter

YAML，紧贴文件首行 `---` … `---`。所有字段可选，缺省按下表。

```yaml
---
source:     atrium | favilla | quicknote | manual | obsidian | track | ai
url:        <可选>
ts:         <ISO8601>
agent:      zephyr | ai | <runtime_id>
authors:    [zephyr, ai]          # 累积作者集合
visibility: both                  # both | human | ai
editable:   both                  # both | human | ai | none
tags:       [a, b]
---
```

| 字段       | 默认   | 说明                                              |
|------------|--------|---------------------------------------------------|
| visibility | `both` | `human` = AI surface 隐藏；`ai` = 人侧 surface 隐藏 |
| editable   | `both` | `none` = 谁都不许改（如 `track/` 产出）           |
| authors    | `[]`   | 写入时若不在列表内则追加；不删                    |

**强制规则**：
- `track/*.md` 文件必须有 `editable: none`（防误改）。
- 私有文件 (`visibility: human` 或 `ai`) 不进 share 接口默认路由。
- frontmatter 缺失视为 `visibility: both, editable: both`。

## 4. 段落级落款

按**段落**（空行分隔的块）落款，不到字符级。

格式：行尾追加 HTML 注释标记，渲染时人眼几乎不可见、机器可解析。

```md
这是我刚写的一段话。<!-- @zephyr 2026-05-14 14:23 -->

AI 改写后的下一段，承接上文。<!-- @ai 2026-05-14 14:25 -->
```

规则：
- 每段最多一个落款标记，写在段尾。
- 若改动既有段落：**追加**新标记，不删旧的；多个标记串在一行末尾，按时间从左到右。
  ```
  ...这一段被两人都改过。<!-- @zephyr 14:23 --> <!-- @ai 14:25 -->
  ```
- 时间用本地时间 `YYYY-MM-DD HH:MM`（与 git commit 一致即可，不强求秒）。
- 作者名取值：`zephyr` | `ai` | `<runtime_id>`（如 `cc`、`copilot`、`codex`）。
- `track/` 产出**不打段落落款**——记录官的章节级总结由 `authors:` frontmatter 标注即可。

正则（机器侧）：`<!--\s*@(\S+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*-->`

## 5. 段落 block（append 类文件）

`desk/YYYY-MM-DD.md`、`studio_ai_inbox/YYYY-MM-DD.md` 这类多条 append 文件，每条用如下 markdown block（与现有 `_studio_format_block` 已实现一致）：

```md
## HH:MM · <source> · <agent>
> selected text line 1
> selected text line 2

note body...

source: https://...
tags: #studio/inbox
```

新条目用空行分隔。

## 6. 时间衰减渲染（记录官 recall）

`track/<name>.md` 按 `# ## ### ####` 自然分层。`recall(name, since=None)` 返回字符串，按距今时间折叠：

| 距今     | 保留层级               |
|----------|------------------------|
| ≤ 7 天   | 全文                   |
| 8–30 天  | 保留到 `##`            |
| 31–90 天 | 保留到 `#`             |
| > 90 天  | 仅 `#` 标题列表        |

实现细节进 `src/fiam/track/recall.py`（P1 落地）。

## 7. Fiam 入流

- Studio channel = `studio`，`delivery = "record_only"`（见 `plugins/studio/plugin.toml`）。
- 任何 `/studio/*` 写入产生一条 `record_only` 事件：`{channel: "studio", actor, source, rel_path, commit_sha, private: bool}`。
- AI 侧 share/quicknote 由 AI 自己的 tool-use 帧负责入流，不重复记录。
- **绝对约束**：Studio 操作永远不叫醒 AI。

## 8. 保留 / 拒绝写入

| 路径模式                     | 行为             |
|------------------------------|------------------|
| `.git/...`                   | 拒绝             |
| `inbox/...` (显式 target)    | 拒绝（AI 私有）   |
| `track/...`（非记录官来源）   | 拒绝              |
| 非 `.md` 后缀                | 拒绝              |
| 路径越界（`..` / 绝对）       | 拒绝              |
| `shelf/<source>/<slug>.md` 已存在 | 拒绝覆盖     |

## 9. 变更约定

改本文件需在 PR 描述（或 commit message）写明：
- 改了哪个章节
- 哪些 surface 需要跟随改（plugin / track / share / reader）

---

— v0.1 · 2026-05-14
