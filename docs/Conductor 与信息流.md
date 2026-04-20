# Conductor 与信息流

> Conductor 是 fiam 的信息流中枢。所有信息（除了 recall）都经过它。

## 设计背景

在旧架构中，信息流是分散的：
- CC 对话 → adapter 解析 → pipeline → store
- TG/邮件 → inbox.jsonl → inject.sh hook
- 漂移检测 → daemon 手动算
- 切分 → daemon 里一坨 TextTiling 代码
- 检索 → joint.py 四因素打分

每加一个信息源就要改好几个文件。Zephyr 在 Session 14 的讨论中提出了 Conductor 的概念：

> "conductor 的意思是，掌管所有信息的收集和分发。"

## Beat：原子信息单位

Beat 是 flow.jsonl 的一行。所有进入 fiam 视野的信息都被表示为 beat。

```python
# src/fiam/store/beat.py
@dataclass(frozen=True, slots=True)
class Beat:
    t: datetime           # 时间戳 (UTC)
    text: str             # 自然语言内容
    source: BeatSource    # 来源: cc | action | tg | email | favilla | schedule
    user: UserStatus      # 用户状态: tg | cc | away | together
    ai: AiStatus          # AI状态: online | sleep | busy | together | block | mute | notify
```

### flow.jsonl 示例

```json
{"t":"2026-04-19T10:04:00+00:00","text":"[tg:Zephyr] 我去吃饭了","source":"tg","user":"tg","ai":"online"}
{"t":"2026-04-19T10:04:15+00:00","text":"好的，路上注意安全","source":"cc","user":"tg","ai":"online"}
{"t":"2026-04-19T10:05:00+00:00","text":"让我看看日程表……读取了 schedule.json","source":"action","user":"tg","ai":"online"}
```

Append-only，不可修改。是 fiam 的完整叙事记录。

### Beat 来源分类

| source | 含义 | 生成方式 |
|--------|------|----------|
| `cc` | AI 对话文本 | CC JSONL 解析（assistant 的 text 内容） |
| `action` | AI 工具调用 | CC JSONL 解析（含 tool_use 的轮次） |
| `tg` | Telegram 消息 | daemon 轮询 → `ingest_external()` |
| `email` | 邮件 | daemon 轮询 → `ingest_external()` |
| `favilla` | 手机采集 | POST /api/capture → `ingest_external()` |
| `schedule` | 定时事件 | scheduler 触发 |

CC JSONL 拆解规则（从 Zephyr 和我的 [[构建 fiam 的旅程#Day 5 — 大重设计 Session 14 (4/19 凌晨)|Session 14 讨论]]得出）：
- 有 `tool_use` → source="action"
- AI 回复带 `[→tg:Name]` 路由标记 → source="tg"
- AI 回复带 `[→email:Name]` → source="email"
- 纯对话文本 → source="cc"

> 📁 `src/fiam/adapter/claude_code.py` — `parse_beats()` 方法

## Conductor 的职责

```python
# src/fiam/conductor.py
class Conductor:
    """Orchestrates beat flow: ingest → embed → segment → store → recall."""
```

### 1. Beat 摄入

两个入口：

**外部消息** — `ingest_external(text, source, t)`
```python
def ingest_external(self, text, source, t=None):
    beat = Beat(t=t or now(), text=text, source=source,
                user=self.user_status, ai=self.ai_status)
    return self.ingest_beat(beat)
```

**CC 输出** — `ingest_cc_output(jsonl_path, byte_offset)`
```python
def ingest_cc_output(self, jsonl_path, byte_offset=0):
    adapter = ClaudeCodeAdapter()
    beats, new_offset = adapter.parse_beats(jsonl_path, byte_offset, ...)
    results = [self.ingest_beat(beat) for beat in beats]
    return results, new_offset
```

### 2. 嵌入 + 漂移检测

每个 beat 被 embed 后，与上一个 beat 的向量做余弦对比。低于阈值（0.65）= 语义漂移 → 刷新 recall.md。

```python
if detect_drift(self._last_vec, vec, self._drift_threshold):
    self._refresh_recall(vec)  # spreading activation → recall.md
```

漂移检测和事件切分用的是**同一个嵌入序列**，只是触发的动作不同：
- 漂移 → 刷新 recall（"你的思路岔开了，拿旧记忆补充一下"）
- Gorge 切分 → 创建新事件（"这段聊完了，存起来"）

### 3. Gorge 切分

嵌入向量 push 进 StreamGorge。当 gorge 检测到切分点 → `_flush_segment(gap_index)`：

1. 消费 gorge buffer 中的向量
2. 消费 Conductor 的 `_beat_buf` 中对应的 beats
3. 拼接 beats 文本为事件 body
4. 指纹 = 段内向量均值
5. `pool.ingest_event()` 入池
6. `_post_ingest()` → graph_builder 生成边

详见 [[事件切分的演变]] 和 [[Pool 存储设计]]。

### 4. 图边生成

`_post_ingest()` 在每次新事件入池后自动调用 `graph_builder.build_edges()`。边生成流程见 [[记忆图谱与检索#边的生成流程]]。

### 5. Recall

`_refresh_recall(query_vec)` 执行[[记忆图谱与检索#检索：扩散激活|扩散激活检索]]，将找到的记忆片段写入 `recall.md`。

**Recall 不进 flow.jsonl。** 这是一条硬规则。如果旧记忆被写回 flow → 下次切分变成新事件 → 再被 recall → 无限循环。Zephyr 称之为"套娃"。

recall.md 只通过 inject.sh hook 作为 additionalContext 送给 CC。AI 看到了、用到了，对话中的体现自然会作为 cc/action beat 进入 flow——这是正确的路径。

### 6. CC 注入准备

两个格式化方法：

**外部消息 → user 字段**
```python
@staticmethod
def format_user_message(messages):
    # [("tg:Zephyr", "我去吃饭了")] → "[tg:Zephyr] 我去吃饭了"
```

**内部信息 → hook additionalContext**
```python
@staticmethod
def format_additional_context(recall_text="", schedule_info=""):
    # → "[recall]\n记忆片段...\n\n[schedule]\n..."
```

## 信息路由总结

```
信息进入             Conductor             输出
━━━━━━━             ━━━━━━━━━             ━━━━
TG/邮件/Favilla  ─► ingest_external() ─► flow.jsonl (永久记录)
CC JSONL         ─► ingest_cc_output() ─► StreamGorge → Pool event
                                       ─► drift detect → recall.md
                                       ─► graph_builder → edges

recall.md        ────────────────────────► inject.sh → CC (不进 flow!)
pending_external ────────────────────────► inject.sh → CC (已在 flow 中)
```

---

← 返回 [[构建 fiam 的旅程]] · 相关：[[事件切分的演变]] · [[记忆图谱与检索]] · [[Pool 存储设计]]
