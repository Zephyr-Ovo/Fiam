# fiam — 流动注入式情感记忆

AI agent 的长期记忆系统。与 Claude Code 并行运行——监听对话 session，实时切分事件，构建带类型边的记忆图谱，通过扩散激活注入相关记忆。

## 架构（v2 — Session 14 重设计）

```
Claude Code session
       │
       ├── JSONL 日志 ──► Conductor ── Gorge (TextTiling) ──► Pool (事件)
       │                     │                                    │
       │                     ├── 漂移检测 ──► recall hook         ├── fingerprints.npy
       │                     ├── beat → flow.jsonl                ├── cosine.npy
       │                     └── embed (bge-m3)                   └── edges (PyG)
       │
       └── Hooks ◄──── 注入 recall 到 additionalContext
                 ├──── 发送外部消息 (TG/邮件)
                 └──── session 启动时注入日报
```

### 核心概念

- **Beat** — `flow.jsonl` 中的原子信息单元。`{t, text, source, user_status, ai_status}`
- **Conductor** — 信息流中枢：beat 摄入 → 嵌入 → Gorge 切分 → Pool 存储 → recall
- **Gorge** — TextTiling 深度切分，峰谷确认。实时将 beat 流切成事件
- **Pool** — 统一 5 层存储（取代旧的分散 store/）
- **扩散激活** — 图检索：种子节点 → 沿边传播 → 概率选择（不是 top-k）

### Pool 存储层

| 层 | 格式 | 内容 |
|---|---|---|
| 内容 | `events/<id>.md` | 事件正文 |
| 元数据 | `events.jsonl` | `{id, t, access_count, fingerprint_idx}` |
| 指纹 | `fingerprints.npy` | N × 1024 矩阵 (bge-m3) |
| 相似度 | `cosine.npy` | N × N 两两余弦相似度 |
| 边 | PyG `edge_index.npy` + `edge_attr.npy` | 有类型有向边 (temporal/semantic/causal/remind/elaboration/contrast) |

### Beat 来源

`cc`（对话）· `action`（工具调用）· `tg` · `email` · `favilla`（手机端）· `schedule`

## 特性

- **实时事件切分** — Gorge 监听 beat 嵌入流，通过 TextTiling 深度 + 峰谷确认触发事件切割
- **语义漂移检测** — 相邻 beat 余弦相似度低于阈值 → recall hook 触发
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
  conductor.py          ★  # Beat 摄入 → 嵌入 → gorge → pool → recall
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
  fiam.py                  # CLI: init, start, stop, scan, status
  dashboard_server.py      # Web 控制台后端
  fiam_lib/
    daemon.py              # 主循环：轮询、session 管理、Conductor 调度
    postman.py             # TG/邮件收发
    scheduler.py           # 定时任务

dashboard/                 # SvelteKit 5 + Svelte runes + Tailwind 4
  src/routes/graph/        # 3D 力导向图谱（Canvas 2D）
  src/routes/events/       # 事件列表 + 详情
  src/routes/flow/         # Beat 流查看器

developer/hooks/           # CC hook 脚本
  inject.sh                # recall 注入 (UserPromptSubmit)
  outbox.sh                # outbox 消息提取 (Stop)
  boot.sh                  # 日报注入 (SessionStart)
  compact.sh               # 归档摘要 (PostCompact)
```

## 命令

| 命令 | 说明 |
|---|---|
| `fiam init` | 交互式配置——生成 `fiam.toml` |
| `fiam start` | 启动 daemon（监听 session，轮询通道） |
| `fiam stop` | 优雅关闭 |
| `fiam scan` | 一次性导入 CC session 历史 |
| `fiam status` | 查看存储计数 + daemon 状态 |

## 配置

复制 `fiam.toml.example` → `fiam.toml`（或运行 `fiam init`）。

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

- **Favilla** (`android/favilla/`) — Android 信息采集 app：选中文本 + 批注 → POST /api/capture
- **Limen** (`devices/limen/`) — ESP32S3 可穿戴设备：摄像头 + 屏幕 + WiFi，物理世界感知锚点

## 许可证

MIT
