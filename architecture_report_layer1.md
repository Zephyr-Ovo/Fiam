# fiam-code 项目总体架构报告

本报告基于代码与项目配置的客观事实，对当前项目架构进行第一层（总体架构与组件关联级别）的总结。整个系统以 Claude Code (CC) 作为智能体交互核心，使用 MQTT 总线接入外部信道，所有信息统一流入 `flow.jsonl`，再按 `memory_mode` 选择标注期人工流水线或自动记忆流水线。

> **2026-04-24 更新**：信道层已从「daemon 直接轮询外部 API」迁移为「MQTT 总线 + 独立 bridge 进程」拓扑。topic 规范：`fiam/receive/<source>` 入站、`fiam/dispatch/<target>` 出站；broker 为 Mosquitto，绑 `127.0.0.1:1883`。详见 [DEVLOG.md](DEVLOG.md) 中「2026-04-24 MQTT 总线落地」一节。

> **2026-04-25 更新**：新增 plugin registry + marker registry。功能性接入统一由 `plugins/<id>/plugin.toml` 描述，可通过 `fiam plugin enable/disable <id>` 开关；出站 marker 改为泛化 `[→target:recipient]`，由 manifest 的 `dispatch_targets` 解析。

> **2026-05-05 更新**：旧 bot chat 路由已完全退休；当前 live channel 以 email、Favilla/app、Limen/XIAO 为准。

## 1. 核心架构逻辑设计
系统摒弃了单一的“对话循环”模型，采用**双态驱动模型**：
- **主动态 (Interactive)**：用户直接交互，通过 CC Hook 机制注入上下文。
- **后台态 (Background/Wake)**：由 `daemon.py` 控制调度事件循环，通过无状态的 `claude -p` 后台唤醒调用。

核心设计哲学是将一切信息输入均视为原子颗粒（**beat**），流入统一日志（`flow.jsonl`）。标注期默认 `memory_mode = "manual"`：Conductor 只冻结 beat 向量，事件切点由 console 人工标注，DeepSeek 负责命名和边建议。`memory_mode = "auto"` 时才启用 drift/Gorge/Pool/recall 自动链路。

## 2. 主要功能组件及其职责

### 2.1 流量路由与枢纽 (Conductor)
**路径**：`src/fiam/conductor.py`
无状态信息枢纽（Hub）。
- **逻辑关联**：所有来自频道的输入（Email、Favilla/app、Limen/XIAO）和 LLM 输出（CC JSONL 日志）均由 `Conductor` 接收。
- **业务职责**：
  - 写入源流日志 `flow.jsonl`（beat 概念）。
  - 调用 Embedding 引擎并把 beat 级冻结向量写入 `store/features/`。
  - 在 manual 模式到此停止，避免自动切割污染训练标注。
  - 在 auto 模式继续执行 drift 检测、Gorge 切割、Pool 写入与 recall 回调。

### 2.2 记忆分段与存储层 (Gorge & Pool)
**路径**：`src/fiam/gorge.py`, `src/fiam/store/`
统一的记忆持久化和切割。
- **语义分割器 (StreamGorge)**：
  - **核心算法**：TextTiling 深度打分变体算法。通过对连续 Beat 的向量进行相似度断层计算（结合确认阈值），自动判断话题结束并“切刀”。
  - 仅在 auto 模式运行；manual 标注期由 console 人工给切点。
- **统一存储模型 (Pool)**：
  - 由 `pool/events/*.md`，`events.jsonl`，`fingerprints.npy`，`cosine.npy` 和 PyG 边矩阵组成。
  - manual 确认后会按切点创建事件；相邻事件 30 分钟内建立弱 temporal 边，DeepSeek 边覆盖同 pair 弱边。

### 2.3 记忆检索引擎与图激活 (Retriever & Graph)
**路径**：`src/fiam/retriever/`, `src/fiam/annotator.py`
网络联想机制，非传统 RAG。
- **图构建 (graph_builder / annotator)**：auto 模式可由 graph_builder 处理新事件候选边；manual 标注期由 annotator 在人工切点确认后批量调用 DeepSeek，返回 event names 与 typed edges。
- **图激活提取 (Spreading Activation)**：
  - **算法**：接收当前滑动向量窗口（Seed 种子），映射到矩阵中最接近的事件，沿节点边权重传导概率。
  - 不使用 Top-K 和 MMR。命中概率（大于 0.4）高的事件写入 `recall.md`。

### 2.4 主循环与 todo 队列 (Daemon & Todo)
**路径**：`scripts/fiam_lib/daemon.py`, `todo.py`
系统的绝对控制中心与生命周期管理者。
- **Daemon**：
  - 代码唯一的真实 Event Loop。**订阅** MQTT `fiam/receive/+`（不再轮询），唤醒 CC（通过 `claude -p` 命令系统子进程），决定外部交互时机（交互拦截或记录 `pending_external.txt`）。
  - 维护唤醒生命周期，提取 `CC` jsonl 中的 `WAKE/SLEEP` tag，触发唤醒与长期休眠流程。
- **Todo queue**：
  - 稍后任务维护队列（`todo.jsonl`），带有指数退避（Backoff）、过期归档与最大尝试次数控制。

### 2.5 下游通信管道 (Bus + Bridges + Postman)
**路径**：`src/fiam/bus.py`, `src/fiam/plugins.py`, `src/fiam/markers.py`, `scripts/bridges/`, `scripts/fiam_lib/postman.py`, `plugins/`, `channels/`
- **MQTT 总线 (Bus)**：薄封装 paho-mqtt，统一 `fiam/receive/<source>` 入站 / `fiam/dispatch/<target>` 出站 topic。QoS 1 + persistent session，daemon 重启不丢消息。
- **Plugin registry**：扫描 `plugins/*/plugin.toml`，决定 source/target 是否启用，以及 marker alias 到 dispatch topic 的解析。禁用插件后 daemon 入站、Conductor 出站与 bridge 运行时都会跳过对应能力。
- **Marker registry**：解析 AI 输出中的 `[→target:recipient]`，不再在 daemon 中硬编码 channel。
- **独立 Bridge 进程**：`bridge_email.py` 由 systemd 管理：拉远端 API → 发 `fiam/receive/<source>`；订 `fiam/dispatch/<target>` → 调 postman 实际投递。daemon 不再持有任何外部 API 凭证或 socket。
- **外发投递机制 (Postman)**：仍是底层邮件协议库（SMTP/IMAP），现仅由 bridge 调用，不被 daemon 直接持有。
- **Outbox 历史路径**：`outbox/*.md` 仍由 `outbox.sh` hook 写入（CC 侧），后续会由 conductor 转译成 `fiam/dispatch/*` 消息。

### 2.6 LLM 介入与 Hook 钩子体系 (Hooks Adapter)
**路径**：`scripts/hooks/*`, `src/fiam/adapter/`
使用纯外接 Shell/PS1 脚本干扰和接管 CC 系统调用。
- **交互上下文接管**：
  - `inject.sh / inject.ps1` 负责劫持 `UserPromptSubmit`，自动拼接刚才产生的 `recall.md`（记忆）与 `pending_external.txt`（通道的新消息），以 `additionalContext` 的 JSON 格式暗中喂给 CC。
  - `outbox.sh` 负责监听 `Stop` 生命周期截获消息落盘到 `outbox/` 供 Postman 寄出。

### 2.7 外部感知 API 与可穿戴端 (Dashboard & Limen/Favilla)
**路径**：`dashboard/dashboard_server.py`, `channels/limen/`, `channels/favilla/`
信息侧载层。
- **Dashboard API**：提供轻量服务器，供前端 SvelteKit 展示图谱、以及开放 `/api/capture` 接入端。
- **边缘设备**：
  - Limen (ESP32)：通过 WiFi 对接 `/api/capture` 执行图像和环境数据采集；获取 `/api/wearable/reply` 作为纯展示屏（带设备交互接口但不执行代码解析）。
  - Favilla (Android)：原生 Kotlin App，将选中文本或备忘推入统一入口。

### 2.8 标注与训练产物 (Annotator)
**路径**：`src/fiam/annotator.py`
远端推理和重训练准备。
- 当前不直接使用本地库调用，而是要求使用配置中的 `RemoteEmotionClassifier` 调用 DO (DigitalOcean) 独立服务器地址及端口的模型（go_emotions与中文开源模型）。
- Dashboard 标注确认时会把 event/drift cut 与 DeepSeek 边建议写入本地 `training_data/`；该目录属于运行时/训练产物，不再进入源码提交。

---

## 3. 清理状态与剩余边角
旧 CLI 与旧分散 store 已清理：`pre/post/session/scan/feedback/graph/rem/import/self-profile` 入口被移除，`HomeStore/GraphStore/formats` 及相关 fiam_lib 旧模块已删除。当前保留的命令为 `init/start/stop/status/settings/add-home/remove-home/clean/find-sessions/plugin`。

仍需关注的边角：
- `sweep_outbox` 仍有一条直接调用 postman 的历史路径，后续应完全转为 `fiam/dispatch/*`。
- `scripts/fiam_lib/jsonl.py` 仍保留 Claude Code session 游标辅助，和 Adapter 层有重叠但仍被维护命令使用。
- `scripts/harden_server.sh` 与 `scripts/templates/awareness.md` 属于部署/提示参考周边，不在 daemon 主路径内。旧的 AW、训练抽取与服务器检查脚本已移入 `archive/legacy_tools/`，只作参考。