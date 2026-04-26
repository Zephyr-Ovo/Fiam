# fiam — 流动注入式情感记忆

AI agent 的长期记忆系统。与 Claude Code 并行运行：把所有信息写入 append-only flow，冻结 bge-m3 beat 向量用于训练，再通过人工或自动切割构建带类型边的记忆图谱。

## 架构（v2 — 标注期 manual-first）

```
Claude Code session
       │
      ├── JSONL 日志 ──► Conductor ──► flow.jsonl + 冻结 beat 向量
      │                     │
      │                     ├── manual: console 人工切点 + DeepSeek 边
      │                     └── auto: drift + Gorge + Pool + recall
       │
       └── Hooks ◄──── 注入 recall 到 additionalContext
                 ├──── 发送外部消息 (TG/邮件)
                 └──── session 启动时注入日报
```

### 核心概念

- **Beat** — `flow.jsonl` 中的原子信息单元。`{t, text, source, user_status, ai_status, meta?}`；嵌入与切点只看 `text`，发送者、URL、路由目标等放在 `meta`。
- **Conductor** — 信息流中枢：beat 摄入 → flow 持久化 → 冻结向量 → 可选自动记忆流水线
- **FeatureStore** — beat 级 bge-m3 冻结向量库，位于分片式 `store/features/`，用 beat hash 幂等索引
- **Gorge** — TextTiling 深度切分，峰谷确认；仅在 `memory_mode = "auto"` 时运行
- **Pool** — 统一 5 层存储（取代旧的分散 store/）
- **扩散激活** — 图检索：种子节点 → 沿边传播 → 概率选择（不是 top-k）
- **Annotator** — console 人工标 event/drift 两类切点，批量触发 DeepSeek 命名和边建议，确认后入 Pool

### Pool 存储层

| 层 | 格式 | 内容 |
|---|---|---|
| 内容 | `pool/events/<id>.md` | 事件正文 |
| 元数据 | `events.jsonl` | `{id, t, access_count, fingerprint_idx}` |
| 指纹 | `fingerprints.npy` | N × 1024 矩阵 (bge-m3) |
| 相似度 | `cosine.npy` | N × N 两两余弦相似度 |
| 边 | PyG `edge_index.npy` + `edge_attr.npy` | 有类型有向边 (temporal/semantic/causal/remind/elaboration/contrast) |

### Beat 来源

`cc`（对话）· `action`（工具调用）· `tg` · `email` · `favilla`（手机端）· `schedule`

### 功能插件协议

功能性入口统一用 `plugins/<id>/plugin.toml` 注册；基础设施（dashboard、网页、git diff、flow、Pool、recall）不作为插件单位。入站统一发布到 `fiam/receive/<source>`，出站统一由 AI marker（如 `[→tg:Iris] ...`）解析到 `fiam/dispatch/<target>`。禁用某项功能时改 manifest 的 `enabled = false`，daemon、Conductor、bridge 都会按该开关跳过收发。

当前 manifests：`tg`、`email`、`favilla`、`xiao`、`app`、`voice-call`、`device-control`、`ring`、`mcp`。详细协议见 [docs/plugin_protocol.md](docs/plugin_protocol.md)。

## 特性

- **标注期人工切割** — console 标 event/drift 切点，已处理 flow 区间写入 `store/annotation_state.json` 后锁定
- **冻结特征采集** — 每条 beat 只保存一次到 `store/features/` 分片向量库
- **实时事件切分** — auto 模式中 Gorge 监听 beat 嵌入流，通过 TextTiling 深度 + 峰谷确认触发事件切割
- **语义漂移检测** — auto 模式中相邻 beat 余弦相似度低于阈值 → recall hook 触发
- **图扩散激活检索** — 从滑动向量找种子节点，沿边传播、权重连乘、概率激发
- **多通道** — Telegram、邮件、Favilla（Android 分享意图）、ActivityWatch
- **Web 控制台** — SvelteKit 5 仪表盘（Catppuccin 深色主题），3D 力导向图谱 + 边编辑，事件 CRUD，flow 查看器
- **Hook 注入** — 4 个 CC hook（UserPromptSubmit, Stop, SessionStart, PostCompact）
- **轻量部署** — ML 依赖可选（`pip install -e ".[ml]"`）；ISP 无 GPU，嵌入走远程 API

## 安装

```bash
git clone https://github.com/Zephyr-Ovo/Fiam.git && cd Fiam
uv sync                              # 基础依赖（不含 torch）
uv sync --extra ml                   # 含 torch/transformers（本地嵌入用）
uv run python scripts/fiam.py init   # 交互式配置向导
uv run python scripts/fiam.py start  # 启动 daemon
```

需要 [uv](https://astral.sh/uv) 和 [Claude Code](https://claude.ai/code)。

远程嵌入（推荐）：在 GPU 服务器部署 `serve_embeddings.py`，在 `fiam.toml` 中设置 `embedding_backend = "remote"`。

## 目录结构

```
src/fiam/
  config.py                # FiamConfig + fiam.toml 解析
  conductor.py          ★  # Beat 摄入 → flow + 冻结向量；可选 auto gorge/pool/recall
  plugins.py            ★  # plugin.toml manifest 扫描 + enable/disable registry
  markers.py            ★  # [→target:recipient] 出站 marker 解析
  gorge.py              ★  # TextTiling 深度切分（批量 + 流式）
  store/
    beat.py             ★  # Beat 数据类 + flow.jsonl 读写
    pool.py             ★  # Pool 5 层存储
  retriever/
    spread.py           ★  # 图扩散激活（种子→传播→选择）
    graph_builder.py    ★  # 统一边生成 + DS 命名/类型化
    embedder.py            # 嵌入器（本地/远程双模式）
  adapter/
    claude_code.py         # CC JSONL → Turn/Beat 解析

scripts/
  fiam.py                  # CLI: init, start, stop, status, clean, find-sessions
  dashboard_server.py      # Web 控制台后端
  fiam_lib/
    daemon.py              # 主事件循环 + CC session 管理
    maintenance.py         # clean + find-sessions
    postman.py             # TG/邮件协议 helper
    scheduler.py           # 定时任务

dashboard/                 # SvelteKit 5 + Svelte runes + Tailwind 4
  src/routes/graph/        # 3D 力导向图谱（Canvas 2D）
  src/routes/events/       # 事件列表 + 详情
  src/routes/flow/         # Beat 流查看器

scripts/hooks/             # CC hook 脚本
  inject.sh                # recall 注入 (UserPromptSubmit)
  outbox.sh                # outbox 消息提取 (Stop)
  boot.sh                  # 日报注入 (SessionStart)
  compact.sh               # 归档摘要 (PostCompact)

channels/
  tg/stickers/             # TG 表情包索引
  favilla/                 # Android 信息采集 app
  limen/                   # ESP32 可穿戴设备

plugins/                   # 功能插件 manifest（可接入/禁用）
  tg/ email/ favilla/ xiao/ app/ voice-call/ device-control/ ring/ mcp/
```

## 命令

| 命令 | 说明 |
|---|---|
| `fiam init` | 交互式配置——生成 `fiam.toml` |
| `fiam start` | 启动 daemon（监听 session，订阅 MQTT 入站） |
| `fiam stop` | 优雅关闭 |
| `fiam status` | 查看存储计数 + daemon 状态 |
| `fiam clean` | 重置生成的 store 数据 |
| `fiam find-sessions` | 调试 Claude Code JSONL session 路径 |
| `fiam plugin list` | 列出功能插件 manifest |
| `fiam plugin show <id>` | 查看某个插件的 topic、能力、鉴权、延迟说明 |
| `fiam plugin enable/disable <id>` | 启用/禁用插件收发 |

## 配置

复制 `fiam.toml.example` → `fiam.toml`（或运行 `fiam init`）。

关键项：`[conductor].memory_mode = "manual"` 表示标注期只写 flow + 冻结向量；`"auto"` 才运行 drift/Gorge/Pool/recall 自动链路。DeepSeek 边建议复用 `[graph]` 配置，默认读取 `FIAM_GRAPH_API_KEY`。

## 部署拓扑

```
本地 (Windows)      Relay (LA)             ISP (Irvine)           DO (SF)
  开发环境              跳板机                AI 主机                 嵌入服务
  无 CC session         纯 SSH 转发           fiam daemon + CC       bge-m3 :8819
       └─── SSH ProxyJump ──►│◄─── SSH ──────┘
                              │
                              └──── SSH ──────► DO
```

- **ISP**：CC 运行、fiam daemon、全部处理。家用宽带，无数据中心标记
- **DO**：纯推理服务器（bge-m3 + emotion），4GB RAM，无状态可重建
- **Relay**：LA 跳板，纯 SSH 转发
- **本地**：开发机，不跑 CC session

## 相关项目

- **Favilla** (`channels/favilla/`) — Android 信息采集 app：选中文本 + 批注 → POST /api/capture
- **Limen** (`channels/limen/`) — ESP32S3 可穿戴设备：摄像头 + 屏幕 + WiFi，物理世界感知锚点

## MCP / App / 路由原则

MCP 是 AI 主动查工具/资源的协议，不是后台 hook。Claude Code 可以通过 hooks/JSONL 深度接入；Claude API 由 Fiam 组装 identity envelope；Claude app 如果不能 hook，就主要通过 MCP/API 主动查询 Fiam。身份意识不放在某个 Claude 客户端里，而放在 `home/self/`、`flow.jsonl`、Pool 和一次性 `recall.md`。

三端路由：Claude app 用 MCP 主动查；自制 app/web 走 Fiam API（入站 `app`、出站 `[→app:Iris]`）；代码/仓库/长任务走 Claude Code。自动触发记忆保存在服务器 flow/Pool；AI 主动写的知识放 `home/self/`、`home/world/` 等文字空间并 git 同步。Website/dashboard 只是展示和编辑层，不作为私密记忆源头。

## 许可证

MIT
