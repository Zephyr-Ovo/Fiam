# Fiam 标记与工具协议

> 适用范围：fiam 通过 CC runtime（`claude -p`）和 API runtime 与模型交互时，AI 输出里允许出现的非自然语言信号。

## 1. 原则

| 类型 | 形式 | 协议特性 | 适用场景 |
|------|------|----------|----------|
| 标记 | text 内嵌 XML 标签 | 单向；解析后剥离；不消耗额外 API 轮次 | 发消息、状态、可见性、调度 |
| 工具 | 原生 `tool_use` block | 双向；必须有 `tool_result` 回包 | 取数据、写文件、查信息、需要 ID 回执的调用 |

所有 Fiam 自定义信号统一走 XML。

## 2. 标记集合

### `<send to="channel:address">...</send>`

```xml
<send to="favilla:Zephyr">收到，已记录。</send>
<send to="email:zephyr@example.com">报告附后。</send>
<send to="limen:screen">message:短句</send>
```

`to` 左侧是 dispatch target，右侧是收件人或设备地址。解析后内容投递到 `fiam/dispatch/<channel>`，标记本身从当前回复剥离。

### `<cot>...</cot>` / `<lock/>`

`<cot>` 是 AI 主动写入的可见思考摘要，不嗅探自由文本；一轮可多次出现。`<lock/>` 锁定本轮 thought 展示，客户端只显示摘要/状态。

### `<hold/>` / `<hold>reason</hold>`

表示撤回本轮可见回复，并自动排一个 `hold_retry`。`reason` 会写入 retry 提示。

### `<wake at="..."/>` / `<todo at="...">...</todo>` / `<sleep at="..."/>`

时间用项目时区或完整 ISO。`wake` 和 `todo` 写入 `self/todo.jsonl`；`sleep` 表示计划进入 sleep 状态，同轮多个以最后一个为准。

### `<state value="..."/>`

```xml
<state value="mute" until="2026-05-08T22:00:00+08:00" reason="专注"/>
<state value="notify"/>
<state value="busy" reason="长任务中"/>
```

`value` 支持 `block`、`mute`、`notify`、`sleep`、`busy`、`together`。状态写入 `self/ai_state.json`。

### `<route family="..."/>`

```xml
<route family="gemini" reason="math/code fallback"/>
```

设置后续若干轮模型 family 选择。它不传递上下文；跨 runtime 上下文由 `store/transcripts/{channel}.jsonl` 统一承载。

## 3. 实现位置

- `src/fiam/markers.py`：XML marker parser。
- `src/fiam/runtime/turns.py`：`<send>` 拆分为 dispatch/message beats。
- `src/fiam/runtime/prompt.py`：共享 transcript messages 装配。
- `scripts/fiam_lib/app_markers.py`：app-side `<cot>` / `<lock/>` / `<hold>` 处理。
