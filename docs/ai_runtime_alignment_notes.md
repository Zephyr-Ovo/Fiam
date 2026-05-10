# AI Runtime / Tooluse / Naming 整理笔记

日期：2026-05-10

目标：把 AI 侧能看到、能使用的概念整理成一套低摩擦规则。代码可以逐步适配，但 AI 不应该被迫理解多套命名、多套工具协议、多套应用入口。

## 已基本敲定

### 1. App 和 Feature 分离

`app` 是承载端，`feature` 是功能。

- `favilla` 是手机端 app。
- `atrium` 是桌面端 app。
- `tlon` 是数字花园 app。
- `chat` 是可被多个 app 注册的功能，不属于 Favilla。
- `stroll` 是出门/位置/摄像头/地图场景；其中的对话性质内容应进入 chat 同步流，只带 stroll 来源/上下文标记。
- `dashboard` 是只读看板功能。
- `studio` 是编辑/摘录/时间轴功能。

整理目标：不要再出现 `favilla.chat` 这种把功能绑死到 app 的协议命名。

### 2. 身份命名

- `user_name` 应可配置；必须写死时默认 `Zephyr`。
- AI 展示名固定为 `ai`，不再可配置；技术部署字段（域名、邮箱、系统用户、Android 包名等）保留稳定 ID。
- `fiet` 不再作为身份名，只作为域名/部署/服务字段。
- 协议和存储里优先保存 role / metadata，展示给 AI 或用户时再渲染名字。

### 3. Engine 和 Model

- `engine=cc`：Claude Code / 订阅制 / 低成本 / 高权限工具环境，但系统指令不可完全控制。
- `engine=api`：OpenRouter / Vertex 等 API 来源 / 高自由度 / prompt 可控 / 成本不一定低。
- `auto` 是配置策略，不作为 AI 侧概念。
- 具体 Gemini、Claude、DeepSeek、fallback 由 config 决定，不让 AI 额外理解一套 `model_family`。

### 4. Flow / Transcripts / Event

- `flow` 是全量信息流和备份，不承担主要追溯查询职责。
- 各功能可按字段从 flow 自取需要的信息。
- AI 记忆召回主要查 `event`，不是查 flow。
- `transcripts` 是 AI 上下文缓存，服务 token 管理、对话连贯、缓存命中。
- `history` 命名应逐步退场，统一为 `transcripts`。

### 5. 向量与记忆

- BGE-M3 当前部署在独立服务器，通过 API 调用。
- BGE-M3 对个人场景不够准确，当前主要作为召回 seed 的一部分。
- seed 需要向量相似 + 关键词等混合确定。
- graph / edge 是召回关系的重要依据。
- 保存向量计算结果的长期目标：数据量足够后，微调 BGE-M3 或外接 MLP，替代人工裁切和漂移检测的一部分。

## XML / 标记协议

XML 的目标不是“标准化漂亮”，而是降低 AI 认知摩擦：AI 写起来简单、读起来清楚、系统好解析。

### 1. COT

敲定形式：

```xml
<cot>这里是可展示给用户的思考摘要</cot>
<lock/>
```

规则：

- `<cot>` 可多段。
- 不需要 `show/end`。
- 只要本轮任意位置出现 `<lock/>`，本轮所有 cot 全部锁定。
- lock 是事后生效，因为 LLM 输出前并不知道自己后面会不会想锁。

### 2. Todo

敲定形式：

```xml
<todo at="2026-05-11 10:00">检查 transcripts 同步协议</todo>
```

规则：

- 所有未来任务都归入 todo。
- 重复任务也放进 todo 的规则里，不另设 schedule。

### 3. Sleep / Wake

当前定义：

```xml
<sleep at="2026-05-10 23:30" />
<wake at="2026-05-11 07:30" />
```

规则：

- `sleep` 是计划什么时候睡。
- `wake` 是计划什么时候醒。
- wake 是相对于睡觉的醒来，不是普通触发 AI。
- 普通提醒/任务用 todo。
- AI 醒着时可以随时制定或更新 sleep 时间，后写覆盖前写。
- 到 sleep 时间后，系统触发一轮；那轮 AI 应写 wake。如果 AI 不想睡，只要更新 sleep 时间。
- `sleep` 不需要 reason。

### 4. Hold

先做最小实现：

```xml
<hold/>
<hold all/>
```

规则：

- `<hold/>`：摘掉本轮本来要发给用户的正文。
- `<hold all/>`：本轮全部不执行、不发送、不解析动作。
- 使用 hold 后自动创建一个 30 秒后的 todo，让 AI 重来。
- hold 的原始输出保留在上下文/记录里，不是删除；下一轮 AI 应能理解自己刚才 hold 了什么。
- 暂不做 target、release、drop、replace 等复杂字段。

## Tooluse 方向

核心目标：API 侧尽量逼近 CC 原生 tooluse，而不是发明一套全新的 API 工具语言。

### 1. CC 是既定事实

- CC 的 tooluse、系统提示、权限机制由 Claude Code 决定。
- 我们不能统一 CC，只能让 API runtime 模仿 CC。
- CC 适合低成本、高权限、多工具、多步任务。
- CC 的 XML 只能在最终输出后被 fiam 解析；CC 原生 tooluse 会更早执行。

### 2. API Runtime 目标

API runtime 应做成 `CC-like free agent`：

- prompt 干净，不灌 Claude Code 那套 coding assistant 指令。
- 工具名和参数尽量模仿 CC。
- 最大自由度优先，不默认设置大量禁区。
- 风险由用户接受；工程层只保留防卡死一类基础保护，例如超时、最大工具轮数、输出截断。

### 3. API 工具倾向

先尽量使用 CC 风格工具名：

- `Read`
- `Bash`
- 后续可加：`Write`、`Edit`、`Glob`、`Grep`

fiam 能力尽量通过 CLI 暴露，让 CC 和 API 都能用类似方式调用：

```bash
fiam query event ...
fiam query transcripts ...
fiam query ring ...
fiam query activity ...
fiam act capture_photo
fiam act view_camera
fiam act browser_snapshot
fiam act xiao_screen "pause"
```

这样 AI 的心智模型保持一致：

- 需要本轮立刻知道结果：用 tooluse，例如 `Read` / `Bash`。
- 不需要本轮知道结果：用 XML。

### 4. XML 和 Tooluse 的边界

XML 适合：

- `<cot>`
- `<lock/>`
- `<todo>`
- `<sleep>`
- `<wake>`
- `<hold>`
- 低成本状态/展示/作息/任务控制

Tooluse 适合：

- 读文件
- 查 event
- 查 transcripts
- 查 ring / activity
- 拍照
- 看照片
- 获取 browser snapshot
- 执行命令
- 任何“必须知道结果才能继续当前回答”的动作

## 仍在讨论

### 1. API 工具集最终范围

倾向先给 API `Read` + `Bash`，再按需要加 `Write/Edit/Glob/Grep`。

未定：

- API 的 `Bash` 是完全真实 shell，还是先由 runtime 直接执行并记录。
- API 是否需要完全模拟 CC 的工具结果格式。
- 图片文件进入 API 后，是直接多模态输入，还是先走 vision 描述。

### 2. Browser

browser 是连续控制，不适合被设计成普通一次性 XML action。

未定：

- browser 是否主要通过 API tooluse loop 执行：
  `snapshot -> action -> snapshot -> action`
- 还是保留独立 autonomous browser session。
- 普通 chat 中是否只允许启动 browser session，而不直接执行 browser 子动作。

当前倾向：browser 是 loop 能力，不要再用全局普通 XML 一步步点。

### 3. Stroll

stroll 是强实时，不等同 browser。

已澄清：

- ring 和 activity 只是信息源，可查，无动作能力。
- stroll 有现场动作，如拍照、看摄像头、屏幕显示、地图标记。

未定：

- 拍照/看图/回答是否统一走 tooluse。
- 小屏显示、地图标记这类无需结果的动作是否仍保留 XML。
- stroll 的实时 loop 和 chat transcripts 如何同步得最轻。

### 4. Transcripts 策略

两种方案仍待定：

- 滚动窗口：始终保留最近 N 条/最近 N tokens，连贯简单。
- 增长到上限再压缩：缓存命中更好，但压缩质量会影响 AI 理解。

### 5. CC 流式渲染

CC `stream-json` 能拿到 tool events，但当前 app 更像完整 result 后假流式。

待定：

- 是否实时转发 CC tool_use/tool_result 到 app。
- CC 回复是否只展示“步骤卡片 + 最终回复”，不强求 token 级流式。

## 整理代码时的落点

优先处理这些混乱源：

1. `history` / `transcript` 命名统一为 `transcripts`。
2. app 和 feature 解耦：chat 不再绑定 Favilla。
3. `scene/source/channel` 混用整理，避免 AI 侧看到 scene。
4. `action` 命名过载清理：区分 tooluse、XML 标记、执行 result。
5. `user_name` 继续配置化；AI 展示名固定为 `ai`，配置项 `ai_name` 已移除。
6. API runtime 改造成 CC-like free agent，而不是另一套轻量受限工具协议。
7. fiam 能力收敛成 CLI / tool 层，供 CC 和 API 共同使用。

