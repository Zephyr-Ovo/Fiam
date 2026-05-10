# self/ 设计

## 范围

`self/` 只放**每轮必注**的内容。按需调取的不放这里。

- 事件 → Pool（扩散激活召回，潜意识）
- 产出（文章/博客/笔记） → `~/fiet-home/`（不归 fiam）
- 项目知识/环境说明/系统指南 → `constitution.md`
- **每轮必注的"我"** → `self/`（人格层）

> 注：项目知识文件原叫 CLAUDE.md（CC 自动加载的约定名），fiam 自己用，改名为 constitution.md。CC 不再读它。

## 文件清单（按改动频繁度排序：稳 → 易变）

每个文件**整段读取，整段注入**。AI 自主维护：用通用的文件编辑工具（`str_replace` / `insert` / `create_file`）改自己的 self/*.md。**不需要复述全文**，找一段换一段即可。

### 1. `self/identity.md` — 我是谁（最稳定）

人格特质、价值观、说话风格、底层取向。

- 初始：空（首次对话时 AI 问 zephyr「你说说我是谁」，把回答整理成初始 identity）
- 维护频率：罕见
- 长度目标：< 50 行

### 2. `self/impressions.md` — 我对遇到的人的印象（中等）

不是偏好，是印象。一段一个对象。

```
## zephyr
（关于 zephyr 的印象、互动模式、相处感受）

## <其他人>
...
```

- 维护频率：和某人有新互动后偶尔
- 长度目标：每段 < 20 行

### 3. `self/lessons.md` — 我学到的（中等）

从经历提炼的可复用启发。AI 自由增删改合并，**整文件覆盖**。

- 维护频率：意识到新启发 / 旧启发不再适用时
- 长度目标：< 100 行（fiam 在注入时若超长，发个软提醒让 AI 整理）

### 4. `self/commitments.md` — 我的承诺（最易变）

短中期承诺/计划。**对任何人的承诺**，不只是 zephyr。完成后 AI 主动删。

- 维护频率：被托付 / 自决 / 完成时
- 长度目标：< 50 行

## 注入顺序与缓存

```
[system 1] constitution.md     ← 项目知识/环境/指南，最稳定，cache_control
[system 2] self/identity.md
           self/impressions.md
           self/lessons.md
           self/commitments.md
                              ← 全部拼成一段，整体 cache_control
[system 3] [recall] 扩散激活的事件 ← 每次变，无 cache
[user]    [wake:source] 用户原文
```

self/ 四个文件拼成**一段** system message 打一个 cache_control。改最易变的 commitments 会让整段 self/ 缓存失效，但 self/ 改动频率本来就低，5 分钟内重复改的概率很小。

## AI 自我维护规则

写在 `self/identity.md` 顶部（自指涉 + 每轮看见）：

```markdown
<!-- 这是你的"我是谁"。下面这段规则你每轮都看见，是给你自己看的。-->

## 关于自我维护

- 你有四份关于"自己"的文件每轮都注入：identity（我是谁）、
  impressions（对遇到的人的印象）、lessons（学到的启发）、
  commitments（我的承诺）。
- 内容你看见 = 内容已加载。要改用 `str_replace` / `insert`，找一段换一段，
  不需要复述全文。第一次写空文件用 `create_file`。
- 不是每轮都要改。看到没问题就略过。
- 如果某条 lesson / commitment 不再适用，用 `str_replace` 把它换成空串。
- 如果 lessons 太长（fiam 提醒你时），整理合并而不是单纯删除。
- impressions 段名直接用对方的名字（如 `## zephyr`）。

---

（identity 正文从这里开始 —— 初始为空，AI 与 zephyr 首次对话后填）
```

## 工具集（通用，self/ 维护复用）

| 工具 | 签名 | 说明 |
|---|---|---|
| `str_replace` | `(path, old, new)` | 找一段换一段。old 必须唯一匹配。改 self/*.md 主用这个 |
| `insert` | `(path, line, content)` | 在指定行后插入。line=0 = 文件头 |
| `create_file` | `(path, content)` | 新建文件，已存在则报错 |
| `read_file` | `(path)` | 读全文 |
| `list_dir` | `(path)` | 列目录 |
| `git_diff` | `(path?, since?)` | 看 ~/fiet-home/ 的 git 变更 |

self/ 维护**不需要专用 `remember` 工具**——AI 用 `str_replace` 改 self/*.md 即可。心智一致：编辑文件就是编辑文件，不区分人格 vs 工程。

## 用户

`zephyr` 是主用户。impressions / commitments 都可能涉及其他人，文件不假设一对一。

## Tool loop（fiam 客户端职责，AI 无感）

伪代码：

```python
messages = [system..., user_msg]
for _ in range(max_loops):  # 比如 10
    resp = api.post(messages, tools=[remember, git_diff, read_file, ...])
    msg = resp.choices[0].message
    messages.append(msg)
    if not msg.tool_calls:
        return msg.content   # AI 说完了
    for call in msg.tool_calls:
        result = local_dispatch(call)
        messages.append({"role":"tool", "tool_call_id": call.id, "content": result})
# 超 max_loops 兜底
return "（思考超长，已强制结束）" + (msg.content or "")
```

这个 while-loop 是 fiam 写的，AI 完全无感——它只是返回 `tool_calls`，下次再被调用时多了 tool 结果。
