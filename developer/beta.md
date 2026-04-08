# fiam 内测命令清单 v0.1

前置要求: Python ≥ 3.11, uv, Claude Code（daemon 相关命令）

---

## 环境激活（一次性，让 `fiam` 直接可用）

`uv sync` 安装后，`fiam` 二进制在 `.venv` 里。激活 venv 后就不需要 `uv run` 前缀：

**Windows（PowerShell）**
```powershell
# 进入项目目录
cd fiam-code

# 激活 venv（当前会话有效）
.venv\Scripts\Activate.ps1

# 验证
fiam --help
```

永久生效（写入 PowerShell profile）：
```powershell
Add-Content $PROFILE "`nSet-Location F:\fiam-code; .venv\Scripts\Activate.ps1"
```

**Linux / macOS（ISP 服务器或本地）**
```bash
cd fiam-code
source .venv/bin/activate

# 或永久写入 .bashrc / .zshrc
echo 'export PATH="$HOME/fiam-code/.venv/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

激活后，以下所有命令直接用 `fiam`，不需要 `uv run`。

---

## 两种运行模式

**本地模型（默认，适合有 GPU 或 ≥4GB RAM 的环境）**

```toml
# fiam.toml
emotion_provider = "local"
language_profile = "multi"   # 下载 bge-m3 + Chinese-Emotion-Small + GoEmotions
```

首次运行 `fiam post` 时自动下载，约 ~2.2GB：
- bge-m3（embedding）~1.5GB
- Chinese-Emotion-Small ~300MB
- GoEmotions ~400MB

**API 模式（推荐 ≤4GB RAM 服务器，或只想快速体验）**

```toml
# fiam.toml
emotion_provider = "api"
narrative_llm_provider = "anthropic"
narrative_llm_model = "claude-3-5-haiku-20241022"
narrative_llm_api_key_env = "ANTHROPIC_API_KEY"
```

只下载 bge-m3（~1.5GB）。情感分类通过 Anthropic API 进行，成本约 $0.005–0.02/session。
CC 用户已有 Anthropic key，可直接复用。

命令完全一致，两种模式切换只改 `fiam.toml`，不需要修改命令。

---

## 0. 安装

```bash
cd fiam-code
uv sync
# 首次 sync 会下载依赖，torch 约 800MB

# GPU 用户（CUDA）：
uv sync --extra cuda

# 验证入口安装成功
fiam --help
# 期望: 看到 15 个子命令列表
```

---

## 1. init（交互式初始化）

```bash
fiam init
```

- 引导设置 `home_path`、`ai_name`、`user_name`、`language_profile`
- 生成 `fiam.toml` + `store/` 目录结构
- 已有 `fiam.toml` 会提示是否覆盖

期望: 完成后 `fiam.toml` 出现在项目根目录

---

## 2. settings（配置查看/编辑）

```bash
# 交互模式
fiam settings

# 非交互批量修改
fiam settings --set top_k=8 idle_timeout_minutes=15

# 还原
fiam settings --set top_k=5 idle_timeout_minutes=10
```

期望: 修改后 `cat fiam.toml` 能看到新值

---

## 3. post（手动处理测试对话 — 核心功能）

```bash
# 内置 fixture，测试情感提取
fiam post --test-file test_vault/fixtures/emotional_gradient.json --debug

# 其他 fixture
fiam post --test-file test_vault/fixtures/data_loss.json --debug
fiam post --test-file test_vault/fixtures/data_creation.json --debug
fiam post --test-file test_vault/fixtures/session377.json --debug
```

- 完整 pipeline：情感分类 → 事件提取 → embedding → 写入 `store/`
- 首次运行自动下载模型（见上方"两种运行模式"）
- `--debug` 打印每个 pair 的门控判定细节（arousal / novelty / elaboration 分数）

期望: 看到 `Extracted N events`，`store/events/` 下出现新 `.md` 文件

---

## 4. pre（手动 recall 检索）

```bash
fiam pre --debug
```

- 需要 `store/` 里已有事件（先跑过 `post`）
- 检索最相关事件 → 合成 recall → 写入 `home_path/recall.md`

期望: `home_path` 下出现 `recall.md`，内容是带时间提示的记忆片段

---

## 5. scan（批量导入历史 JSONL）

```bash
fiam scan --debug

# 强制重扫（忽略 cursor，重新处理所有文件）
fiam scan --force
```

- 扫描 Claude Code 的 `~/.claude/projects/{home-slug}/` 目录
- cursor 去重：已处理的文件自动跳过
- 需要本机有 Claude Code 且有历史对话

期望: 看到 `N files to process, M skipped`

---

## 6. reindex（重建所有向量）

```bash
fiam reindex --debug
```

- 遍历 `store/events/` 所有 `.md`，重新计算 embedding
- 不改变事件内容，只更新 `.npy` 文件
- 用于切换 `embedding_model` 或 `language_profile` 后

期望: `store/embeddings/` 下所有 `.npy` 被刷新

---

## 7. feedback（事件权重微调 TUI）

```bash
# 需要真实终端（非 IDE 输出面板）
fiam feedback

# 指定展示数量
fiam feedback -n 15
```

操作: `↑↓` 选择事件，`→` 增加权重 (+0.1)，`←` 降低 (-0.1)，`Enter` 保存退出

- 调整 `user_weight` 字段 [0.2, 2.0]，直接乘在检索得分上
- 连击有上限封顶，不会无限累加

期望: 修改后对应事件 `.md` frontmatter 出现 `user_weight` 字段

---

## 8. graph（Obsidian 可视化）

```bash
fiam graph --debug

# 调整相似度阈值（默认 0.75，越低链接越多）
fiam graph --threshold 0.65
```

- 按 cosine 相似度在事件间建立 `[[wikilink]]`，输出到 `home_path` 下
- 读取 `store/events/*.md` + `store/embeddings/*.npy`
- 不触碰情感模型，纯 numpy 相似度计算
- 保留 `.obsidian/` 配置目录，只清理 `*.md`

期望: 用 Obsidian 打开 `home_path` 可看到事件图谱

---

## 9. rem（记忆整理 TUI — 需要 LLM）

```bash
# 先开启 LLM
fiam settings --set llm_enabled=true llm_api_key_env=ANTHROPIC_API_KEY

# 运行（需要真实终端）
fiam rem --debug

# 调松阈值（更多合并候选，默认 0.82）
fiam rem --threshold 0.75
```

操作: `Enter` = LLM 合并，`s` = 跳过，`q` = 退出；合并后 `r` = 重新生成，`Enter` = 确认写入，`s` = 放弃

- 自动聚类 cosine > threshold 的相似事件，逐簇展示
- 合并后原始事件移入 `store/events/_archived/`

---

## 10. daemon 全流程（需要 Claude Code）

```bash
fiam start
# 启动后台守护进程
# 先跑一次 pre_session + recall 注入
# 每 30s poll JSONL 检测对话活动
# 无活动超过 idle_timeout（默认 30min）后触发 post_session

fiam status
# 期望: 显示 PID、运行时间、事件数

fiam stop
# 期望: 进程退出，PID 文件清理
```

---

## 11. find-sessions（调试用）

```bash
fiam find-sessions
# 列出 Claude Code 项目目录下所有 JSONL 文件
# 无 Claude Code 时会提示找不到目录
```

---

## 12. clean（重置 store）

```bash
fiam clean       # 有确认提示
fiam clean -y    # 跳过确认（脚本/CI 用）
```

删除 `store/events/` 和 `store/embeddings/` 下所有文件。`fiam.toml` 和 hook 文件不受影响。

---

## 效果判断基准

### graph 图谱质量

`fiam graph --threshold 0.75` 生成后用 Obsidian 打开：
- **期望**: 情感相关事件（比如同一天焦虑相关对话）聚类在一起
- **调参**: threshold 降低 → 更多连线（过度网状时调高）；升高 → 孤立节点变多（稀疏时调低）
- **阈值参考**: 0.85=严格（只有几乎相同话题才连），0.75=默认，0.65=宽松

### 情感分类准确性

`fiam post --test-file test_vault/fixtures/emotional_gradient.json --debug` 输出中查看：
- 每个 pair 的 `a=` (arousal)、`v=` (valence) 数值
- **高唤醒期望场景**: 数据丢失、争论、重要发现 → arousal > 0.6
- **低唤醒期望场景**: 日常任务、中性问答 → arousal < 0.4
- `emotional_gradient.json` fixture 设计了明确的情感坡度，应能看到数值从低到高的变化

### 检索召回质量

跑 `fiam pre --debug` 后查看 `recall.md`：
- **期望**: 内容与当前 `home_path` 最近对话主题相关
- **调参**: `fiam settings --set top_k=8` 召回更多；`similarity_threshold` 提高 → 只返回高度相关的

### 本地 vs API 模式对比

同一个 fixture 分别用两种模式运行，对比 `store/events/` 输出的 `arousal` 值：
- local 模式: WDI 双语模型，中文精度更高
- api 模式: haiku 标注，English 文本精度更平均；速度取决于网络延迟

---

## 速查：不需要 Claude Code 就能测的命令

```
init, settings, post --test-file, pre, reindex, feedback, graph, rem, clean
```

## 速查：需要 Claude Code 的命令

```
start, stop, status, scan, find-sessions
```
