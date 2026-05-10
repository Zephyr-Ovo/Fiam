# Fiam 标记与工具协议（v0.3 · 进行中）

> 状态：起草中。✅ 已拍板，🟡 进行中，⬜ 未讨论。
> 适用范围：fiam 通过 CC runtime（`claude -p`）和 API runtime 与模型交互时，AI 输出里允许出现的"非自然语言"信号；以及 fiam 暴露给 AI 的"工具"。

## 1. 设计原则 ✅

### 1.1 信号承载方式按"是否需要 runtime 回包"分

| 类型     | 形式                 | 协议特性                           | 适用场景                                     |
|----------|----------------------|------------------------------------|----------------------------------------------|
| **标记** | text 内嵌 XML 标签   | 单向；解析后剥离；不消耗额外 API 轮次 | 状态宣布、可见性控制、暂存、告别              |
| **工具** | 原生 `tool_use` block | 双向；必须有 `tool_result` 回包；多 1 轮 API 成本 | 取数据、写文件、查信息、要 ID 回执的长延迟调度 |

### 1.2 与 Claude 训练分布对齐 ✅

- Claude.app 的系统 prompt 自身就大量使用 XML（`<claude_behavior>`、`<refusal_handling>` 等），**XML 是 Claude 的母语级结构**。
- Claude API 的 `tool_use` 是独立 typed content block，**不是 text 里的 XML**；CC runtime 显示 `<function_calls>` 之类只是 UI 渲染，模型实际产生的是结构化块。
- 结论：fiam 自定义信号走 XML 标签时模型最顺手；走工具时形式必须等同于 CC 原生工具（同一份 tool 列表、同一种调用方式）。

### 1.3 XML 风格规则 ✅

- **有内容**：标准前后夹击，`<hold>...</hold>`、`<final>...</final>`、`<lock>...</lock>`
- **纯信号**：自闭合，`<carry_over to="api" />`、`<later at="..." reason="..." />`、`<sleep until="..." />`、`<mute until="..." />`
- 标签名小写；属性可选；语义明确优先于省 token。

## 2. 标记集合（不是最终）

### 2.1 `<hold>` ✅ 草稿暂存（chat-only）

形态：

```xml
<hold until="2026-05-05T18:00+08:00" reason="等 Zephyr 下班再说">
草稿正文……
</hold>
```

行为：

- AI 在响应里写出 `<hold>` 即代表"本轮整个输出暂不外发"；text/thinking 里的其他内容**全部不投递**给原通道。
- runtime 收到 hold → 落 `store/holds/<id>.md`（含草稿、原 source、原消息、附件、时间）→ 在 `schedule.jsonl` 排一条 `action="app_hold"` 到 `until`。
- 到点 wake：把 hold md 里的原消息和草稿注入下一次 `claude -p` 调用；AI 必须在响应里用 `<final>...</final>` 交最终稿（见 §2.2）。
- AI **不需要记 hold id**——wake 注入的就是要被处理的那一条，daemon 自动关联。

适用范围：

- 仅当当前 beat 来自 **`dialog_capable = true` 的 channel**（Favilla chat 等）。
- 其它来源（schedule 唤醒内部、daemon 维护、wearable stroll、voice-call 等）若 AI 误用 `<hold>`，parser **按字面忽略 + 日志 warn**。
- channel 是否 dialog_capable 在 `plugins/<id>/plugin.toml` 里声明（待加字段）。

上限：连续 hold 链长度上限写 `[chat] max_hold_chain`，超限 daemon 强制把当前草稿原样投递。

并发：允许多个 hold 同时存在，各自 md，wake 互不干扰。

### 2.2 `<final>` ✅ hold 唤醒交付

形态：

```xml
<final>
最终要发给原通道的正文……
</final>
```

daemon 行为：

- 命中关键词时，把内部内容投递给 hold md 记录的原通道+收件人。
- 关键词外的 text 视为私下笔记，不进入 Favilla 聊天历史。
- hold 唤醒中没有 `<final>` 且没有新的 `<hold>` 时，daemon 视为未完成，交给现有 schedule 重试/归档机制。
- thinking 块本身就是原生 hidden，元思考天然不漏。

### 2.3 `<lock>` ✅ 思考可见性封存

形态：

```xml
<thinking>
…正常思考…
<lock reason="边界">
不愿外露的那段思考
</lock>
…继续思考…
</thinking>
```

行为：

- **只在 thinking 块内部出现才生效**。text 里出现按字面忽略（讨论这个功能本身时不会误伤）。
- 哪里出现锁哪里：lock 内容封存为"思考过但不展示"；UI 渲染时显示一个静默标记，不展开。
- lock 与 hold 正交：hold 决定本轮是否外发；lock 决定（外发时）的思考可见性。hold 状态下 lock 仍有意义——wake 后投递的最终稿，对应 thinking 中的 lock 段同样不展示。

### 2.4 `<carry_over>` 🟡 告别+换 runtime（标记，不是工具）

形态草案：

```xml
<carry_over to="api" reason="切回日常聊天" />
<carry_over to="cc" reason="需要代码工具" />
```

已确认：

- carry_over = 在当前 runtime 完成最后一次发言 + 触发 runtime 切换 + 把上下文打包传过去。
- 一次性、单向、当前 runtime 任务结束，不需要回包，因此**用标记不用工具**。
- handoff 不再单独存在，合并进 carry_over。
- `to` 目前只允许 `api | cc`，未来需要新 runtime 再扩 enum。
- AI **不参与 delta-context 计算**。daemon 按目标 runtime 自动补足"对方平台不在场期间发生的信息"：
	- API runtime 可直接读 `flow.jsonl` / store，不需要重复注入大段历史。
	- CC/官方 App 这类自带上下文的平台，只注入它缺失期间的增量，避免 flow 重复灌入导致 token 膨胀。

待讨论：触发链、失败回退、是否允许 reason 进入 flow。

### 2.5 `<later>` 🟡 替换 `<<WAKE>>`

方向已定：这是普通的“稍后再处理”信号，不叫 wake ai，也不暴露旧 scheduler 内部字段。

形态：

```xml
<later at="2026-05-05T20:00:00+08:00" reason="提醒 Zephyr 明天的安排" />
```

行为：

- `at` 必填，ISO 8601 时间，带时区。
- `reason` 必填或强烈建议填写，作为到点注入给 AI 的自然语言原因。
- 内部仍写入 `self/schedule.jsonl`，旧 `type=private|notify|check` 仅保留兼容，不再作为 AI 可见协议。
- 旧 `<<WAKE:ISO:type:reason>>` 迁移期保留兼容。

### 2.6 状态标签 🟡 替换 `<<MUTE>>`/`<<SLEEP>>`

方向已定：不用集中式 `<state value="..." />`。AI 可见层先只暴露三个自然动作：睡下、静音、恢复通知。

形态：

```xml
<sleep until="2026-05-06T08:00:00+08:00" reason="晚安" />
<sleep until="open" reason="任务完成" />
<mute until="2026-05-05T22:00:00+08:00" reason="专注" />
<notify />
```

行为：

- `sleep`：退役当前 CC session；`until="open"` 表示等下一次外部事件或计划事件。
- `mute`：外部消息继续入 flow，但不打扰；`until` 统一表示状态到期时间，可省略，省略时直到 `<notify />`。
- `notify`：恢复可通知状态。

内部 `ai_state.json` 仍可兼容旧状态值，但 AI 文本协议不主动暴露 `busy/together/block/online`。

### 2.7 `<cot>` ⬜ 思维链显式包装

待讨论：原生 thinking block 已存在；fiam 是否还需要 `<cot>` 让 AI 在 text 中显式标记"这段是给用户看的思考"。考虑到默认 show、需要时 lock，可能不需要新标签。

## 3. 工具集合（API runtime 已存在 + CC runtime 待对齐）

### 3.1 已存在的 API runtime 工具 ✅

`read_file`、`list_dir`、`write_file`、`create_file`、`str_replace`、`insert`、`git_diff`、`grep_files`、`get_time`、`schedule_wake`、`set_ai_state`。

### 3.2 CC runtime 工具对齐 ⬜

待讨论：CC 那边除了内置 Read/Bash/Edit/Grep/Glob/Write 之外，需不需要把上面这套 fiam 工具也暴露给 CC（用 MCP 还是直接靠 CC 内置工具够了）。

## 4. 待清理

- ✅ 旧 bot chat route 已弃用：runtime 运行面已清理；相关 channel、bridge、plugin 当前不存在；outbox hook 不再接受旧 route；远端旧 bot token 已从环境文件移除。历史讨论稿仅作 archive 语境。
- 🟡 `<<HOLD:>>` 旧标记：现有 `app_markers.py` 仍兼容；新 `<hold>` 会保存草稿到 `store/holds/<id>.md` 并通过 `<final>` 完成交付。
- ⬜ `<<COT:show>>...<<COT:end>>` / `<<COT:lock>>` 旧标记：迁移到 `<lock>` 后弃用。
- 🟡 `<<WAKE:>>`、`<<SLEEP>>`、`<<MUTE>>` 等旧标记：`<later>`/`<sleep>`/`<mute>` 新解析已开始落地，迁移期保留兼容。

## 5. 待加配置

```toml
[chat]
max_hold_chain = 3   # 连续 hold 上限

# plugins/<id>/plugin.toml 新增字段
dialog_capable = true|false   # 是否允许 <hold> 标记生效
```

## 6. 待加代码骨架（未实现）

- `src/fiam/markers.py`：统一 XML 标记 parser（替换分散的 `app_markers.py` / scheduler `WAKE_RE` / `_parse_cot`）。
- `src/fiam/holds.py`：hold md 落库 + wake 注入 + final 投递。
- `src/fiam/runtime/tools.py`：API 工具定义（已部分存在），按 §3 整理。
