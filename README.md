# fiam

Bio-inspired affective memory for AI — 跨会话的情感记忆管道。

---

## 它是什么

fiam 是一个围绕 Claude Code 运行的后台 daemon。它监测对话日志，提取有情感显著性的事件，存入记忆，并在会话中实时注入相关回忆。

AI 不参与记忆管理。记忆的编码、存储、检索、遗忘全部由 fiam 在 AI 之外完成——像海马体一样，工作在意识之下。

---

## 理念

**不扮演，不伪装，不为了拟人而拟人。**

fiam 不伪造"人类式记忆"。AI 知道 recall 来自 hook 注入，正如人类知道自己会遗忘但无法阻止——这种透明是诚实的，不需要隐藏。

回忆的注入像 BGM：背景里浮现的东西，AI 可以回应，可以忽略，不是指令。形式上是 `additionalContext`，不是 user message，不会被混入对话流。

**拒绝 LLM 级联。** LLM 管理 LLM 的记忆会导致风格趋同、细节磨损、幻觉累积。fiam 用小模型分类 + 规则过滤做存储决策，LLM 只在叙事合成环节可选介入。

**事件而非画像。** 存储的不是"用户是怎样的人"，而是"那次发生了什么，当时什么感觉"。

**人格自写。** 没有结构化的价值观文件。AI 自己维护 `self/personality.md`，格式不限。

---

## 架构

```
                       Claude Code 会话
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             │             ▼
         JSONL 日志          │      UserPromptSubmit hook
         (对话记录)          │      (注入 recall.md → additionalContext)
              │             │             ▲
              ▼             │             │
        fiam daemon         │        recall.md
         ┌────────┐        │        (记忆碎片)
         │ 监控    │        │             ▲
         │ JSONL   │        │             │
         │ 变化    │────────┘        语义检索
         └────────┘                      │
              │                     joint_retriever
              │  话题漂移检测               ▲
              │  (余弦相似度 < 0.65)        │
              ▼                     store/events/
         实时更新 recall            (YAML + 正文)
              │                          ▲
              │  空闲超时                  │
              ▼                     post_session
         post_session              (分类 → 过滤 → 存储)
```

### 两条路径

**实时路径（会话中）**：daemon 检测到 JSONL 有新内容 → 提取最近用户文本 → 嵌入 → 与上次 recall 查询比较余弦相似度 → 低于 0.65 = 话题漂移 → 重新检索 → 重写 `recall.md`。UserPromptSubmit hook 在每次用户发送消息时读取 `recall.md` 注入为 `additionalContext`。

**批处理路径（会话后）**：空闲超时后 → 增量解析 JSONL（字节偏移） → 情感分类 → 显著性门控 → 存入事件 → 刷新 recall → 等待下一次活动。

### 显著性门控

事件通过任意一个信号即可存储（any-of）：

| 信号 | 阈值 | 含义 |
|---|---|---|
| emotional | arousal > 0.6 | 情绪显著 |
| novelty | > 0.7 | 与已有记忆语义距离大 |
| elaboration | > 1.5 | 用户文本长度 / 会话中位数（log2） |

Valence（情感极性）被存储但不参与门控。

### 记忆检索

```
base = 0.5 × cosine(query, event) + 0.3 × retention(event)
final = base + 0.2 × temporal_boost(event, top_candidates)
```

遗忘曲线：$R(t) = e^{-t\,/\,(S \cdot 14)}$，$S$ = 强度 ∈ [1.0, 3.0]，被召回时强化。

多样性过滤：贪心去重，跳过与已选事件余弦相似度 > 0.88 的候选，输出 top-5。

### recall.md 格式

```markdown
<!-- recall | 2026-04-06T09:54:20Z -->

- (3天前) 啊啊啊啊数据全丢了三周全白费了！！！ → 别急，先看看有没有备份
- (2小时前) 代码跑通了 → 恭喜！
- (14天前) I spent the entire weekend rewriting the memory system from scratch...
```

干净的记忆碎片，没有角色标记，没有对话格式。短交互用 `→` 连接用户与 AI 的核心语句，长文本只保留用户侧。

---

## Hook 机制

fiam 通过 Claude Code 的 `UserPromptSubmit` hook 实现会话中注入。hook 在每次用户发送消息时触发，读取 `recall.md`，作为 `additionalContext` 返回——进入 system/context 层，不是 user message。

```json
// .claude/settings.local.json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "powershell -File path/to/inject.ps1"
      }
    ]
  }
}
```

AI 知道这些内容来自 hook。这不是 bug，是特性——不需要伪装记忆来源。

---

## 模型

`fiam init` 时选择语言 profile，决定下载哪些模型：

| Profile | 嵌入模型 | 维度 | Arousal 来源 | 适用场景 |
|---|---|---|---|---|
| `multi` ★ | `BAAI/bge-m3` | 1024 | cardiffnlp neutral-proxy | 中英混写、默认推荐 |
| `zh` | `BAAI/bge-base-zh-v1.5` | 768 | cardiffnlp neutral-proxy | 纯中文、低内存 |
| `en` | `BAAI/bge-base-en-v1.5` | 768 | j-hartmann 7类映射 | 纯英文、精细情绪 |

Valence（情感极性）三个 profile 均使用 `cardiffnlp/twitter-xlm-roberta-base-sentiment`（多语言）。

所有模型本地推理，缓存在 `~/.cache/huggingface/`。

> **Arousal 方案**：zh/multi 使用 `1 - P(neutral)` 作为 arousal proxy，语言无关，实测中文"啊啊啊数据全丢了！！！"→ arousal 0.94。en profile 额外加载 j-hartmann 做 7 类 Russell 映射获得更精细的 arousal。

---

## 双目录架构

| 目录 | 作用 | 内容 |
|---|---|---|
| `fiam-code/` | 代码 + 机器数据 | Python 源码、事件存储、向量、日志 |
| home | AI 的家 | `.md` 文件、personality、journal |

home 里只有人类可读的 `.md`。事件、向量、缓存全在 `fiam-code/store/`。

```
fiam-code/
├── src/fiam/          # 管道代码
├── scripts/fiam.py    # 入口
├── store/             # 运行时自动创建
│   ├── events/        # 事件 .md (YAML frontmatter + 正文)
│   ├── embeddings/    # 向量 .npy（维度由 language_profile 决定）
│   └── cursor.json    # JSONL 字节偏移状态
├── .cache/            # 模型权重（HF_HOME，gitignored）
└── logs/sessions/     # 报告 + 追踪

home/
├── CLAUDE.md          # 首次运行自动生成
├── recall.md          # daemon 实时更新的记忆碎片
├── self/
│   ├── personality.md # AI 自写
│   └── journal/       # 自由书写
├── {user_name}/       # 用户空间
└── .gitignore
```

---

## 安装

> **Windows**：hook 脚本为 PowerShell（`inject.ps1`）。
> **Linux/macOS**：hook 脚本为 bash（`inject.sh`）。
> `fiam init` 自动检测平台，生成对应脚本，无需手动区分。

**依赖**
- [uv](https://docs.astral.sh/uv/) — Python 依赖管理器
- [Claude Code](https://claude.ai/code) 已安装并在 PATH 中
- 一个目录作为 AI 的 home（如 `D:\ai-home` 或 `~/ai-home`）

**1. 安装依赖**

```bash
uv sync
```

> Windows 上不要用 conda 的 torch。`uv run` 自动使用 `.venv`。

**1a. 下载模型**（首次，本地存储）

| Profile | 模型 | 大小 |
|---|---|---|
| `multi` ★ | bge-m3 + cardiffnlp | ≈ 3.4 GB |
| `zh` | bge-base-zh + cardiffnlp | ≈ 1.5 GB |
| `en` | bge-base-en + j-hartmann + cardiffnlp | ≈ 1.8 GB |

`fiam start` 首次运行时自动下载所需模型，存储在 `fiam-code/.cache/huggingface/`（项目内本地），缓存后离线可用。

手动下载（以 multi profile 为例）：

```powershell
# 重定向 HuggingFace 缓存到项目目录
$env:HF_HOME = "$(Get-Location)\.cache\huggingface"
$env:HF_ENDPOINT = "https://hf-mirror.com"

uv run python -c "
from sentence_transformers import SentenceTransformer
from transformers import pipeline
SentenceTransformer('BAAI/bge-m3')
pipeline('text-classification', model='cardiffnlp/twitter-xlm-roberta-base-sentiment', top_k=None)
print('Models cached in fiam-code/.cache/huggingface/')
"
```

> **提示**：`fiam init` 会自动设置这个环境变量，你也可以在 PowerShell profile 中写入以默认使用项目本地缓存。

**2. 运行配置向导**

```bash
uv run python scripts/fiam.py init
```

回答几个问题（home 路径、语言 profile、AI 名字、你的名字），自动生成：

```
fiam.toml                           配置文件（gitignored，含个人路径）
{home}/CLAUDE.md                    Claude Code 启动时读取
{home}/.claude/hooks/inject.ps1     记忆 hook 脚本（Windows；macOS/Linux 为 inject.sh）
{home}/.claude/settings.local.json  在 Claude Code 中启用 hook
{home}/.gitignore, self/, {name}/   home 目录结构
```

**3. 在 home 目录启动 Claude Code**

```bash
cd <your-home>
claude
```

必须从 home 目录启动，使 `CLAUDE_PROJECT_DIR` 指向 home，hook 才能找到 `recall.md`。

**4. 启动 daemon（另开一个终端）**

```bash
uv run python scripts/fiam.py start
```

fiam 轮询 Claude Code 的 JSONL 日志。有新活动时检测话题漂移并实时更新 `recall.md`。静默 30 分钟后触发完整 post-session 流程（情感分类 → 事件存储 → 记忆刷新）。

---

### 导入历史会话

如果你有大量 Claude Code 历史对话想导入 fiam：

```bash
uv run python scripts/fiam.py scan
```

处理所有历史 JSONL 文件，提取情感显著事件，并更新 cursor 以防 daemon 重复处理。在 `fiam start` 之前运行一次，耗时取决于历史数量。

> **fiam 从不修改 JSONL 文件。** 它们位于 `~/.claude/projects/`，由 Claude Code 独占，fiam 只读。如果 store 损坏或想全部重来，`fiam clean` + `fiam scan` 即可——历史全部保留，随时重跑。

---

### 所有命令

```bash
fiam init                # 配置向导（运行一次）
fiam start               # 启动 daemon
fiam stop                # 停止 daemon
fiam status              # daemon 状态 + 事件数量
fiam clean               # 重置 store（测试后恢复）
fiam scan                # 一次性历史导入
fiam pre   [--debug]     # 手动运行 pre_session
fiam post  [--test-file <path>]  # 手动运行 post_session
fiam reindex             # 重建嵌入（更换模型后）
fiam find-sessions       # 列出 JSONL 文件
fiam graph               # 生成 Obsidian 图谱  ✦ 见下
```

### 首次运行冒烟测试

```bash
# 1. 用测试 fixture 处理（无需真实 CC 会话）
uv run python scripts/fiam.py post \
    --test-file test_vault/fixtures/emotional_gradient.json --debug

# 2. 运行检索 + 写入 recall.md
uv run python scripts/fiam.py pre --debug

# 3. 查看会话报告
#    logs/sessions/{id}/report.md
```

步骤 1 输出 0 events：arousal 阈值过滤掉了所有内容，试试 `emotional_gradient.json`（专为测试设计，情绪幅度大）。
步骤 2 检索 0 events：`min_event_age_hours=6` 屏蔽了刚写入的事件，在 `fiam.toml` 临时加 `min_event_age_hours = 0`。

---

## 常见问题

**中文情感识别准不准？**

旧版用 j-hartmann，对中文几乎永远返回 neutral（arousal ≈ 0.20），绝大多数中文事件被门控过滤掉。现在 zh/multi profile 改用 cardiffnlp neutral-proxy：`arousal = 1 - P(neutral)`，语言无关。实测"啊啊啊数据全丢了！！！"→ arousal=0.94，不再被漏掉。

**双语用户（中英混写）怎么处理？**

bge-zh 和 bge-en 是独立向量空间，跨语言的 cosine 相似度没有意义。推荐 `multi` profile（bge-m3），统一多语言向量空间，中英混写检索无缝。

**支持 Linux / macOS 吗？**

`fiam init` 自动检测平台：Windows 生成 `inject.ps1`（PowerShell），Linux/macOS 生成 `inject.sh`（bash）。核心管道跨平台，hook 脚本格式不同而已。

**recall.md 会被 prompt injection 污染吗？**

理论上存在：若对话中出现恶意构造文本并通过显著性门控被存储，下次会话会被注入。实际缓解：门控要求 arousal > 0.6 或高 novelty，随意短文不会存入；recall 格式固定，AI 明确知道其来源是 hook context 而非正常对话。作为个人工具这个风险可接受。

**会和 Claude Code 自带的 memory 功能冲突吗？**

不冲突。CC memory 是会话级上下文压缩，fiam 是跨会话情感事件。fiam 只读 JSONL，不操作 CC 内部状态。唯一交叉点是 `additionalContext`，recall 格式固定不会混淆。

**CC 改了 JSONL 格式怎么办？**

JSONL 解析已抽象为 `ConversationAdapter` protocol（`src/fiam/adapter/`），当前实现是 `ClaudeCodeAdapter`。格式变化只需改这一个文件，核心管道不受影响。

**fiam.toml 里有哪些参数可以调？**

| 参数 | 默认 | 说明 |
|---|---|---|
| `language_profile` | `multi` | zh / en / multi |
| `arousal_threshold` | `0.6` | 降低 = 存更多事件 |
| `novelty_threshold` | `0.7` | 降低 = 允许更多相似事件 |
| `min_event_age_hours` | `6` | 测试期间设 0 |
| `top_k` | `5` | recall 最多显示几条 |
| `idle_timeout_minutes` | `30` | 多久没活动触发 post-session |

---

## 项目结构

```
scripts/fiam.py              # 入口 + daemon + recall 实时更新
src/fiam/
  config.py                  # FiamConfig + fiam.toml 读写
  pipeline.py                # pre_session / post_session 编排
  classifier/
    emotion.py               # profile-aware 情感分类（valence + arousal proxy + 启发式兜底）
  extractor/
    event.py                 # 显著性门控（emotional / novelty / elaboration）+ 合并
    signals.py               # 副信道信号（volatility, temperature_gap 等）
  injector/
    claude_code.py           # 写入 recall.md / CLAUDE.md / .gitignore
    home_diff.py             # git diff 检测用户对 home 的物理编辑
  logging/
    report.py                # Markdown 报告
    trace.py                 # JSON 步骤追踪
  personality/
    reader.py                # 读取 self/personality.md
  retriever/
    embedder.py              # profile-aware bge 嵌入（单模型，由 language_profile 决定）
    joint.py                 # 联合检索：语义 + Ebbinghaus + 时序链接
    decay.py                 # 遗忘曲线 + 强度强化
    diversity.py             # 贪心嵌入去重
    temporal.py              # 时序共现链接（4h 窗口）
  store/
    formats.py               # EventRecord dataclass + YAML frontmatter
    home.py                  # 事件文件读写
  adapter/
    __init__.py              # ConversationAdapter Protocol + get_adapter() 工厂
    claude_code.py           # ClaudeCodeAdapter：JSONL 解析 + 字节偏移增量读取
  synthesizer/
    stance.py                # StanceSynthesizer（冷启动叙事合成）
    narrative.py             # 规则碎片 + 可选 LLM 叙事
    dynamics.py              # 对话动态提取
developer/
    README.md                # 开发者工具文档
    debug.md                 # 调试指南 + hook 测试
    hooks/                   # hook 参考脚本
```

---

## EventRecord

```yaml
---
time: 2026-04-03T14:32:00Z
valence: -0.3
arousal: 0.8
confidence: 0.85
access_count: 2
strength: 1.4
last_accessed: 2026-04-05T09:00:00Z
embedding: embeddings/ev_20260403_001.npy
embedding_dim: 1024    # 768 for zh/en profiles, 1024 for multi
tags: [user_name, topic]
links: [ev_20260403_002]
---
```

---

## fiam graph  ✦ 1.0

```bash
uv run python scripts/fiam.py graph                # default threshold 0.75
uv run python scripts/fiam.py graph --threshold 0.6  # looser links
```

Scans the event store. Extracts a word from each event body to use as its node name — no LLM, pure regex, gleefully random: 小橘, debugging, 数据全丢, cosine, 备份找到... Writes them to `store/graph/` as Obsidian-compatible Markdown files. Events whose embedding cosine similarity exceeds the threshold get `[[wikilinks]]` added to each other.

Open `store/graph/` as an Obsidian vault. Switch to Graph View. ✦v✦ 🪄

```
store/graph/
  数据全丢.md   ←[[]]→   备份找到.md
  小橘趴在.md   ←[[]]→   猫又把杯.md
  debugging.md
```

The graph directory is wiped and regenerated fresh every run. It's a read-only view — the event store is never modified.

