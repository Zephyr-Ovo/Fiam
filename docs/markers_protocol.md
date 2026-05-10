# Fiam 标记与工具协议（v2.0）

> 适用范围：fiam 通过 CC runtime（`claude -p`）和 API runtime 与模型交互时，
> AI 输出里允许出现的"非自然语言"信号；以及 fiam 暴露给 AI 的"工具"。

## 1. 设计原则

### 1.1 信号承载方式按"是否需要 runtime 回包"分

| 类型     | 形式                  | 协议特性                                           | 适用场景                                      |
|----------|-----------------------|----------------------------------------------------|-----------------------------------------------|
| **标记** | text 内嵌 XML 标签    | 单向；解析后剥离；不消耗额外 API 轮次              | 状态宣布、可见性控制、暂存、调度              |
| **工具** | 原生 `tool_use` block | 双向；必须有 `tool_result` 回包；多 1 轮 API 成本  | 取数据、写文件、查信息、需要 ID 回执的调用    |
| **路由** | text 内嵌 `[→…:…]`    | 单向；解析后剥离；驱动 daemon 投递到外部通道       | 显式指定收件人/通道                           |

### 1.2 与 Claude 训练分布对齐

- Claude 系统 prompt 自身大量使用 XML，**XML 是 Claude 的母语级结构**。
- Claude API 的 `tool_use` 是独立 typed content block，不是 text 里的 XML。
- 结论：fiam 自定义信号走 XML 标签；工具走原生 tool_use。

### 1.3 XML 风格规则

- **有内容**：标准前后夹击，`<cot>...</cot>`、`<carry_over>...</carry_over>`
- **纯信号**：自闭合，`<hold/>`、`<lock/>`、`<sleep until="..." />`、`<mute until="..." />`、`<notify/>`、`<wake at="..." reason="..." />`、`<todo at="..." reason="..." />`
- 标签名小写；属性可选；语义明确优先于省 token。

## 2. 标记集合

### 2.1 `<hold/>` — 草稿暂存

形态：

```xml
<hold/>
草稿正文……
```

或：

```xml
<hold all/>
全部内容暂不外发……
```

行为：

- AI 在响应里出现 `<hold/>` 即代表"本轮 text 内容暂不外发到原通道"。
- `<hold all/>`：text + thinking 全部不投递。
- 无属性 `<hold/>`：仅 text 暂存，thinking 仍可被记录。
- 标记仅在来源是 **dialog-capable channel**（Favilla 等）时生效；其它来源（schedule、limen、xiao 等）按字面忽略 + 日志 warn。
- 不再使用 `until` / `reason`；hold 状态由后续显式工具调用解除。

### 2.2 `<cot>` — 思维链可见包装

形态：

```xml
<cot>
…思考过程…
</cot>
```

行为：

- 默认渲染为可折叠思考块。
- `<cot>` 块在解析后，从最终对外文本中剥离，仅在 dashboard 视图保留。

### 2.3 `<lock/>` — 思考锁定标记

形态：单标签 `<lock/>`，置于 cot 块内。

行为：

- 标记当前 cot 块为不展开渲染（dashboard 显示一个静默标记）。
- 仅在 cot 块内部生效，text 内出现按字面忽略。

### 2.4 `<wake at="…" reason="…" />` — 调度醒来

形态：

```xml
<wake at="2026-05-08T08:00:00+08:00" reason="提醒早餐" />
```

行为：

- `at` 必填，ISO 8601 + 时区。
- `reason` 必填，到点注入给 AI 作为自然语言原因。
- daemon 写入 `self/todo.jsonl`，到点通过 turn_runner 唤醒新一轮。

### 2.5 `<todo at="…" reason="…" />` — 延迟任务

形态：与 `<wake>` 相同，只是语义上是"提醒任务"而非"唤醒"。

行为：

- 内部统一写 `todo.jsonl`，daemon 到点触发，与 wake 同路径。

### 2.6 `<sleep until="…" reason="…" />` — 进入睡眠

形态：

```xml
<sleep until="2026-05-09T08:00:00+08:00" reason="晚安" />
<sleep until="open" reason="任务完成" />
```

行为：

- 退役当前 CC session；`until="open"` 表示等下一次外部事件或调度。
- `ai_state.json` 写 `state="sleep"`。

### 2.7 `<mute until="…" reason="…" />` / `<notify/>` — 通知开关

形态：

```xml
<mute until="2026-05-08T22:00:00+08:00" reason="专注" />
<notify/>
```

行为：

- `mute`：外部消息继续入 flow，但不打扰；`until` 可省略，省略时直到 `<notify/>`。
- `notify`：恢复可通知状态。
- 内部仅维护三个外露 AI 状态：`sleep` / `mute` / `notify`。

### 2.8 `<carry_over>` — 跨 runtime 交接

形态：

```xml
<carry_over to="api" reason="切回日常聊天">
给下一个 runtime 的简短上下文摘要……
</carry_over>
```

行为：

- 在当前 runtime 完成最后一次发言 + 触发 runtime 切换 + 把上下文打包传过去。
- `to` 允许 `api | cc`。
- delta-context 由 daemon 自动按目标 runtime 补足，AI 不参与计算。

### 2.9 `[→channel:recipient]` — 路由前缀

形态（行首）：

```
[→favilla:Zephyr] 收到，已记录。
[→email:zephyr@example.com] 报告附后。
```

行为：

- 被 `outbound_routes_re` 解析为目标通道 + 收件人。
- 解析后从 text 剥离，daemon 把内容投递到对应 channel。
- 未带前缀的 text 默认回到来源 channel。

## 3. 工具集合（API runtime）

`read_file`、`list_dir`、`write_file`、`create_file`、`str_replace`、`insert`、`git_diff`、`grep_files`、`get_time`、`schedule_wake`、`set_ai_state`。

CC runtime 走 Claude Code 自带 Read/Bash/Edit/Grep/Glob/Write，不重复暴露 fiam 工具。

## 4. 配置

```toml
# plugins/<id>/plugin.toml
dialog_capable = true | false   # 是否允许 <hold/> 标记生效
```

## 5. 实现位置

- `src/fiam/markers.py`：统一 XML 标记 parser（hold / wake / todo / sleep / mute / notify / carry_over / lock）。
- `scripts/fiam_lib/app_markers.py`：app-side cot 解析（`<cot>` / `<lock/>`）。
- `src/fiam/runtime/turns.py`：路由前缀 `outbound_routes_re` 解析。
- `src/fiam/runtime/tools.py`：API 工具定义。
