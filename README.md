# Fiam — 为流动中的对话而设计的情感记忆系统
Fluid Injected Affective Memory

---

## 它是什么

Fiam 是一个围绕 Claude Code 运行的后台 daemon。它监测对话日志，提取有情感显著性的事件，存入记忆，并在合适的时候把几段旧事轻轻带回当前。

AI 不参与记忆管理，也不需要主动决定"要不要查记忆"。记忆的编码、存储、检索、遗忘全部由 fiam 在 AI 之外完成，像潜意识一样浮现。可以被回应，也可以被忽略，淡化 AI 过度元意识带来的抽离感。

---

## 特点

**1. 不让 LLM 管理一切**

让 LLM 总结、压缩、再喂回自己，记忆很容易风格趋同、细节磨损。Fiam 把判断和筛选交给小模型/规则层，把原始事件尽量留下来，用叙事代替情感定义，更像是闪回而非查档案。

**2. 记忆跟着对话流动**

Fiam 持续监测当前话题是否发生漂移，只有在真的"聊到别处"时才重新整理浮现的 recall。既不是每轮都召回，也不是只在开头塞一段旧上下文——更接近"记忆跟着注意力移动"的方式。

**3. 相似度作为底层骨架**

相似度不只用来检索记忆，同时决定 recall 如何排序、何时更新、当前话题是否已经转向。抽掉相似度，Fiam 就不再是 Fiam。

**4. 感知 AI 的环境**

Fiam 记住的不只是对话，还包括 AI home 目录里发生了什么。当你修改了一个文件（比如留了便签、批注了日记），Fiam 直接把极度精简的 git diff 信息递给 AI——自然、轻盈，省 token。

**5. Obsidian 星空图谱 ✦**

`fiam graph` 自动扫描事件存储，生成 Obsidian 兼容的 Markdown 文件，用图谱视图打开即可看到事件关联，生成你和 AI 的专属星空。

---

## Fiam 的观念

**去人类中心化**　在双目录架构里，home 目录完全属于 AI——不是方便人类整理文件的工作目录，而是 AI 生活和积累痕迹的地方。

**保持诚实**　Fiam 接受 AI 了解这一切，知道所谓"回忆"是 hook 注入进来的数据，这并不灾难。存在之间是差异而非差距。

**尊重关系的多样性**　不定义什么才算对的陪伴、协作或共同生活方式。

**结构优先于补丁**　v1.0 支持范围窄，和 Claude Code 强绑定。如果需要，会优先做更深层、更完整的结构，而非快速适配所有场景。

---

## 架构

```
                    Claude Code 会话
                          │
           ┌──────────────┼──────────────┐
           │              │              │
           ▼              │              ▼
      JSONL 日志           │    UserPromptSubmit hook
      (对话记录)           │    (recall.md → additionalContext)
           │              │              ▲
           ▼              │              │
      fiam daemon         │         recall.md
       ┌────────┐         │         (记忆碎片)
       │ 监控   │─────────┘              ▲
       │ JSONL  │                   语义检索
       └────────┘                        │
           │                       joint_retriever
           │  话题漂移检测                 ▲
           │  (余弦相似度 < 0.65)          │
           ▼                       store/events/
      实时更新 recall              (YAML + 正文)
           │                             ▲
           │  空闲超时                    │
           ▼                       post_session
      post_session               (分类 → 过滤 → 存储)
```

**实时路径（会话中）**：daemon 检测到 JSONL 有新内容 → 提取最近用户文本 → 嵌入 → 与上次 recall 查询比较余弦相似度 → 低于 0.65 = 话题漂移 → 重新检索 → 重写 `recall.md`。UserPromptSubmit hook 在每次用户发送消息时读取 `recall.md` 注入为 `additionalContext`。

**批处理路径（会话后）**：空闲超时后 → 增量解析 JSONL（字节偏移）→ 情感分类 → 显著性门控 → 话题分割 → 存入事件 → 刷新 recall → 等待下一次活动。

### 显著性门控

事件通过任意一个信号即可存储（any-of）：

| 信号 | 阈值 | 含义 |
|---|---|---|
| emotional | arousal > 0.6 | 情绪显著 |
| novelty | > 0.7 | 与已有记忆语义距离大 |
| elaboration | > 1.5 | 用户文本长度 / 会话中位数（log2） |

Valence（情感极性）被存储但不参与门控。

### 话题分割（TextTiling Depth Score）

通过门控的 pair 不是各自生成事件，而是先做话题分割——同一话题段内的显著 pair 合并为一个事件。分割算法使用 TextTiling depth score：

1. 对每个相邻间隙，计算前后 window 个 pair 的块嵌入余弦相似度
2. 计算每个间隙的 **depth**（左峰 - 当前 + 右峰 - 当前）——衡量相对凹陷程度
3. 在 depth 的局部极大值处切割（阈值 0.1）

关键区别：不依赖绝对相似度阈值。0.9 降到 0.7 是切割（相对凹陷大），0.5 到 0.5 不切（平稳）。这让渐变漂移不会被误切，而真正的话题转折一定被捕捉。

### 记忆检索

```
base  = 0.5 × cosine(query, event) + 0.3 × retention(event)
final = base + 0.2 × temporal_boost(event, top_candidates)
```

遗忘曲线：$R(t) = e^{-t\,/\,(S \cdot 14)}$，$S$ = 强度 ∈ [1.0, 3.0]，被召回时强化。多样性过滤：贪心去重，跳过与已选事件余弦相似度 > 0.88 的候选，输出 top-5。

### recall.md 格式

```markdown
<!-- recall | 2026-04-06T09:54:20Z -->

- (3天前) 啊啊啊啊数据全丢了三周全白费了！！！ → 别急，先看看有没有备份
- (2小时前) 代码跑通了 → 恭喜！
- (14天前) I spent the entire weekend rewriting the memory system from scratch...
```

干净的记忆碎片，没有角色标记。短交互用 `→` 连接用户与 AI 的核心语句，长文本只保留用户侧。

---

## 双目录架构

| 目录 | 作用 | 内容 |
|---|---|---|
| `fiam-code/` | 代码 + 机器数据 | Python 源码、事件存储、向量、日志 |
| `home/` | AI 的家 | `.md` 文件、personality、journal |

```
fiam-code/
├── src/fiam/          # 管道代码
├── scripts/fiam.py    # 入口
├── store/             # 运行时自动创建
│   ├── events/        # 事件 .md（YAML frontmatter + 正文）
│   ├── embeddings/    # 向量 .npy（维度由 language_profile 决定）
│   └── cursor.json    # JSONL 字节偏移状态
├── .cache/            # 模型权重（HF_HOME，gitignored）
└── logs/sessions/     # 报告 + 追踪

home/
├── CLAUDE.md          # 首次运行自动生成
├── recall.md          # daemon 实时更新的记忆碎片
├── self/
│   ├── personality.md # AI 自写
│   └── journal/
├── {user_name}/       # 用户空间
└── .gitignore
```

---

## 模型

fiam 使用两类本地模型：嵌入（embedding）和情感分类（emotion）。`fiam init` 时选择语言 profile。

### 嵌入模型

| Profile | 模型 | 维度 | 大小 |
|---|---|---|---|
| `multi` ★ | `BAAI/bge-m3` | 1024 | ~2.3 GB |
| `zh` | `BAAI/bge-base-zh-v1.5` | 768 | ~400 MB |
| `en` | `BAAI/bge-base-en-v1.5` | 768 | ~400 MB |

### 情感模型（WDI）

情感分类使用 **Weighted Dimensional Interpolation（WDI）**：将模型输出的完整概率分布加权插值到 Russell circumplex 的 valence-arousal 连续空间，不做 top-1 标签映射。

| 语言 | 模型 | 标签 | 大小 |
|---|---|---|---|
| 英文 | `SamLowe/roberta-base-go_emotions` | 28 | ~500 MB |
| 中文 ★ | `Johnson8187/Chinese-Emotion-Small` | 8 | ~300 MB |
| 中文 large | `Johnson8187/Chinese-Emotion` | 8 | ~2.2 GB |

> 中文 Small 和 Large 使用相同标签，精度差异很小。`fiam init` 默认推荐 Small。

### 磁盘占用汇总

| Profile | 模型组合 | 磁盘 |
|---|---|---|
| `multi` ★（small） | bge-m3 + GoEmotions + Chinese-Emotion-Small | ≈ 3.1 GB |
| `multi`（large） | bge-m3 + GoEmotions + Chinese-Emotion | ≈ 5.0 GB |
| `zh`（small） | bge-base-zh + Chinese-Emotion-Small | ≈ 0.7 GB |
| `en` | bge-base-en + GoEmotions | ≈ 0.9 GB |

所有模型本地推理，缓存在 `fiam-code/.cache/huggingface/`，不污染全局缓存。

---

## 安装

fiam 支持 Windows / macOS / Linux。`fiam init` 自动检测平台，生成对应 hook 脚本。

### 准备工作

**① 安装 uv**

```powershell
# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

重启终端，确认 `uv --version` 能打印版本号。

**② 安装 Claude Code**

前往 [claude.ai/code](https://claude.ai/code) 按官方说明安装。确认 `claude --version` 能正常运行。

**③ 准备 AI 的 home 目录**

```bash
mkdir D:\fiet-home   # Windows
mkdir ~/fiet-home    # macOS / Linux
```

### 安装步骤

**第 1 步：克隆 fiam**

```bash
git clone https://github.com/your-name/fiam-code.git
cd fiam-code
```

**第 2 步：安装 Python 依赖**

```bash
uv sync
```

> 不要用 conda 的 Python / torch（尤其 Windows），会有版本冲突。`uv sync` 会帮你搞定一切。

**第 3 步：下载模型**

国内网络建议先设置镜像：

```powershell
# Windows
$env:HF_ENDPOINT = "https://hf-mirror.com"
$env:HF_HOME = "$PWD\.cache\huggingface"
```

```bash
# macOS / Linux
export HF_ENDPOINT="https://hf-mirror.com"
export HF_HOME="$PWD/.cache/huggingface"
```

以推荐的 `multi` profile 为例下载（约 3 GB）：

```bash
uv run python -c "
from sentence_transformers import SentenceTransformer
from transformers import pipeline
SentenceTransformer('BAAI/bge-m3')
pipeline('text-classification', model='SamLowe/roberta-base-go_emotions', top_k=None)
pipeline('text-classification', model='Johnson8187/Chinese-Emotion-Small', top_k=None)
print('完成！')
"
```

> 也可以跳过，第一次运行 `fiam start` 时会自动下载。

**第 4 步：运行配置向导**

```bash
uv run python scripts/fiam.py init
```

```
Home directory:           ← 输入第③步创建的目录，如 D:\fiet-home
Language profile [multi]: ← 直接 Enter（推荐 multi）
AI name [Fiet]:           ← AI 的名字
Your name [Zephyr]:       ← 你的名字
Enable git? [Y/n]:        ← 推荐 Y
```

向导完成后自动生成：

```
fiam.toml                            ← 配置文件（不要提交 git）
{home}/CLAUDE.md                     ← Claude Code 每次启动时读取
{home}/.claude/hooks/inject.ps1      ← 记忆注入 hook（Windows ps1，其他 sh）
{home}/.claude/settings.local.json   ← 启用 hook
{home}/.gitignore, self/, {name}/    ← home 目录结构
```

**第 5 步：从 home 目录启动 Claude Code**

```bash
cd D:\fiet-home
claude
```

> 必须从 home 目录启动，Claude Code 的 `CLAUDE_PROJECT_DIR` 才会指向 home，hook 才能找到 `recall.md`。

**第 6 步：启动 daemon（另开终端）**

```bash
cd fiam-code
uv run python scripts/fiam.py start
```

看到彩色 `fiam ✦` 启动画面即可。以后每次使用时先启动 daemon，再从 home 目录开 `claude`。

### 导入历史会话

如果已用了一段时间 Claude Code，想把历史对话导入记忆：

```bash
uv run python scripts/fiam.py scan
```

只需运行一次，历史越多耗时越长（几百个会话约几分钟）。fiam 只读 JSONL，从不修改原始记录。

---

## 命令速查

```
fiam init              配置向导（第一次运行）
fiam start             启动 daemon
fiam stop              停止 daemon
fiam status            daemon 状态 + 事件/嵌入数量
fiam clean             重置 store（清空记忆）
fiam scan              一次性历史导入（Claude Code JSONL）
fiam import <file>     导入 Claude Web 导出的 conversations.json
fiam reindex           重建嵌入（更换模型后使用）
fiam graph             生成 Obsidian 回忆图谱  ✦
fiam feedback          交互式事件评价（←👎 →👍）
fiam rem               LLM 辅助事件合并整理
fiam settings          查看/修改 fiam.toml
fiam pre / post        手动触发 pre/post_session（调试用）
fiam find-sessions     列出 Claude Code 的 JSONL 文件
```

---

## 快速验证

```bash
# 处理测试对话，查看是否能提取事件
uv run python scripts/fiam.py post --test-file test_vault/fixtures/emotional_gradient.json

# 运行检索，查看 recall.md 是否生成
uv run python scripts/fiam.py pre

# 查看报告：logs/sessions/{最新目录}/report.md
```

有事件输出 + recall.md 生成 = 安装成功。

| 现象 | 原因 | 解决 |
|---|---|---|
| `post` 输出 0 events | arousal 阈值过滤 | 换 `emotional_gradient.json` |
| `pre` 检索 0 events | 新事件被 `min_event_age_hours=6` 屏蔽 | `fiam.toml` 加 `min_event_age_hours = 0` |
| `fiam start` 说 already running | 上次没正常退出 | 先 `fiam stop`，再 `fiam start` |
| hook 没有触发 | settings.local.json 不在 home/.claude/ | 重新运行 `fiam init` |
| 下载模型太慢 | 没设置镜像 | `HF_ENDPOINT=https://hf-mirror.com` |

---

## 常见问题

**适合什么人用？**
Claude Code 订阅制用户，需要长期情感/记忆管理，话题跨度大，不想牺牲现有 MCP/skills。纯粹用 AI 查资料、写代码的场景 fiam 是冗余的。

**支持哪些系统？**
Windows、macOS、Linux。不支持网页版和手机 APP。`fiam init` 自动检测平台：Windows 生成 `inject.ps1`，Linux/macOS 生成 `inject.sh`。

**硬件要求？**
Fiam 必须在本地运行情感分类模型。英文专用约 500 MB；中文建议 Small（300 MB），推荐至少 2 GB 空闲内存。

**支持 API / 其他平台吗？**
暂不支持。JSONL 处理和 hook 注入与 Claude Code 强绑定，短期内没有跨平台计划。

**双语用户（中英混写）怎么处理？**
推荐 `multi` profile（bge-m3），统一多语言向量空间，中英混写检索无缝。bge-zh 和 bge-en 是独立向量空间，跨语言 cosine 相似度没有意义。

**recall.md 会被 prompt injection 污染吗？**
理论上存在，实际缓解：门控要求 arousal > 0.6 或高 novelty，随意短文不会存入；recall 格式固定，AI 明确知道来源是 hook context。作为个人工具这个风险可接受。

**会和 Claude Code 自带的 memory 功能冲突吗？**
不冲突。CC memory 是会话级上下文压缩，fiam 是跨会话情感事件，唯一交叉点是 `additionalContext`，recall 格式固定不会混淆。

**和已有的 MCP / skills / agent 冲突吗？**
不冲突，除非 skill 同样涉及 hook 注入——cc 支持多注入（追加到末尾），但存在两个记忆召回机制会冗余。

**CC JSONL 格式未来变化怎么办？**
JSONL 解析已抽象为 `ConversationAdapter` protocol（`src/fiam/adapter/`），格式变化只需改 `claude_code.py`，核心管道不受影响。

---

## 配置参数（fiam.toml）

| 参数 | 默认 | 说明 |
|---|---|---|
| `language_profile` | `multi` | zh / en / multi |
| `emotion_provider` | `local` | local = WDI 本地模型；api = LLM API（跳过模型下载） |
| `arousal_threshold` | `0.6` | 降低 = 存更多事件 |
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
    emotion.py               # WDI 情感分类（valence + arousal）
  extractor/
    event.py                 # 显著性门控（emotional / novelty / elaboration）+ 合并
    signals.py               # 副信道信号
  injector/
    claude_code.py           # 写入 recall.md / CLAUDE.md / .gitignore
    home_diff.py             # git diff 检测对 home 的物理编辑
  logging/
    report.py                # Markdown 报告
    trace.py                 # JSON 步骤追踪
  personality/
    reader.py                # 读取 self/personality.md
  retriever/
    embedder.py              # profile-aware bge 嵌入
    joint.py                 # 联合检索：语义 + Ebbinghaus + 时序链接
    decay.py                 # 遗忘曲线 + 强度强化
    diversity.py             # 贪心嵌入去重
    temporal.py              # 时序共现链接（4h 窗口）
    relevance.py             # 相关性评分
  store/
    formats.py               # EventRecord dataclass + YAML frontmatter
    home.py                  # 事件文件读写
  adapter/
    __init__.py              # ConversationAdapter Protocol + get_adapter()
    claude_code.py           # ClaudeCodeAdapter：JSONL 解析 + 字节偏移增量读取
  synthesizer/
    stance.py                # StanceSynthesizer（冷启动叙事合成）
    narrative.py             # 规则碎片 + 可选 LLM 叙事
    dynamics.py              # 对话动态提取
developer/
  README.md                  # 开发者工具文档
  debug.md                   # 调试指南 + hook 测试
  hooks/                     # hook 参考脚本
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
embedding_dim: 1024
tags: [user_name, topic]
links: [ev_20260403_002]
---
```

---

## fiam graph ✦

```bash
uv run python scripts/fiam.py graph                    # 默认阈值 0.75
uv run python scripts/fiam.py graph --threshold 0.6    # 更宽松的连接
```

扫描事件存储，用纯 regex 从每条事件正文中提取一个词作为节点名（小橘、debugging、数据全丢、cosine...），写入 `store/graph/` 作为 Obsidian 兼容的 Markdown 文件。嵌入余弦相似度超过阈值的事件之间加 `[[wikilinks]]`。

用 Obsidian 打开 `store/graph/` 目录，切换到图谱视图。✦

```
store/graph/
  数据全丢.md   ←[[]]→   备份找到.md
  小橘趴在.md   ←[[]]→   猫又把杯.md
  debugging.md
```

每次运行会清空并重新生成，图谱是只读视图，事件存储不会被修改。