# fiam-code 信息流动逻辑报告（第二层）

> 颗粒度：每条信息在代码中的每一次被加工、流转、变形的步骤。不做判断，只陈述事实。

---

## 一、外部消息入站流（以 Email 为例）

### 1.1 bridge_email.py — 拉取 → 发布

`bridge_email.py` 以独立进程运行，每隔 `config.email_poll_interval` 调用一次 `fetch_inbox(config)`。

`fetch_inbox` 通过 IMAP 拉取未读邮件。对每封邮件，提取 `From`、`Subject`、`Date` 和 plain-text body，写入 `inbox/` 归档，并返回 `{"text", "from_name", "t", ...}` 结构给 bridge。

bridge 对返回的每条消息，先检查 `plugins/email/plugin.toml` 是否仍启用；若启用，调用 `bus.publish_receive("email", {...})`。

`publish_receive` 在 `bus.py` 中的行为：向 topic `fiam/receive/email` 发布一条 MQTT 消息，payload 为 JSON 序列化后的字典，包含 `text`、`from_name`、`source`、`t`（ISO 字符串）。`datetime` 对象在此处被序列化为 `isoformat()` 字符串。QoS = 1。

---

### 1.2 MQTT broker → daemon _on_receive 回调

daemon 在启动时用 `bus.subscribe(RECEIVE_ALL, _on_receive)` 订阅了 topic 模式 `fiam/receive/+`。当 broker 将消息推送到该 topic 时，paho 的网络线程回调 `_on_receive(source, payload)`。

`_on_receive` 做以下事情：
- 从 `payload["text"]` 取文本，去掉两端空白；若为空则直接返回，不入队。
- 根据 `payload["source"]` 或 topic 叶子名查询 plugin registry；若该 source 属于 disabled 插件，则记录日志并返回，不入队。
- 从 `payload["t"]` 取时间字符串，尝试 `datetime.fromisoformat()` 解析；若格式异常则用 `datetime.now(timezone.utc)` 代替。
- 将 `{source, from_name, text, t}` 字典放入 `_inbox_q`（一个 `queue.Queue`）。

这一步发生在 paho 的后台网络线程，不在主循环线程中。

---

### 1.3 daemon 主循环 — 队列排空

主循环每 `poll_interval` 秒执行一次。每轮开始时，通过 `_inbox_q.get_nowait()` 循环将队列中所有消息取出，收集到 `all_msgs` 列表。若队列为空，`Empty` 异常终止循环。

`all_msgs` 按 `m["t"]`（消息时间戳）做升序排序，以保证 `flow.jsonl` 的时序与真实世界一致。

---

### 1.4 daemon → Conductor.receive → flow.jsonl + 冻结向量

对 `all_msgs` 中的每条消息，调用 `conductor.receive(msg["text"], msg["source"], t=msg.get("t"))`，因此 flow 中保存的是消息真实时间戳，而不是 daemon 处理时间。

`Conductor.receive` 的行为：
1. 用当前 UTC 时间（若调用方未传入 `t`）、`text`、`source`、当前 `user_status`（如 `"away"`）、当前 `ai_status`（如 `"online"`）和可选 `meta` 构造一个 `Beat` 数据类实例。`meta` 存 sender、url、route target 等路由/来源信息，不参与嵌入与切点。
2. 调用内部方法 `_ingest_beat(beat)`。

`_ingest_beat` 的步骤：

**步骤 a — 写入 flow.jsonl**  
调用 `append_beat(flow_path, beat)`，将 beat 序列化为一行 JSON 追加写入 `flow.jsonl`。Beat 字段包含 `t`（ISO 字符串）、`text`、`source`、`user`、`ai`，以及可选 `meta`。这是所有信息的第一个持久化落点。

**步骤 b — 嵌入与冻结特征**  
调用 `embedder.embed(beat.text)`，将纯内容文本转化为 bge-m3 float32 向量。随后 `FeatureStore.append_beat_vector()` 将向量幂等写入 `store/features/chunks/flow_vectors_*.npy`，并在 `flow_index.jsonl` 记录 beat hash、时间、source、text_hash、model_id、dim、chunk 文件与行号。旧版单文件 `flow_vectors.npy` 仍兼容读取。若 embed 调用抛出异常，beat 已写入 flow.jsonl 但不会产生冻结向量，也不继续走后续流程。

**步骤 c — manual / auto 分流**  
若配置 `[conductor].memory_mode = "manual"`，`_ingest_beat` 到此返回。此时不做漂移检测、不推入 StreamGorge、不写 Pool、不建边。默认标注期使用该模式。

**步骤 d — 漂移检测（auto 模式）**  
若 `_last_vec` 不为 None，且当前 beat 的 source 不是 `"action"`，调用 `detect_drift(last_vec, current_vec, threshold=0.65)`。`detect_drift` 计算两个向量的余弦相似度，若低于 0.65 则触发 `on_drift(current_vec)` 回调。该回调在 daemon 中被绑定为 `_refresh_recall`（见第三节）。`_last_vec` 随后更新为当前向量。

**步骤 e — 推入 StreamGorge（auto 模式）**  
将 beat 追加到 `_beat_buf`（一个内存列表），同时将向量传入 `StreamGorge.push(vec)`。

---

### 1.5 StreamGorge — 流式话题切分

`StreamGorge.push(vec)` 将向量追加到内部的 `_vecs` 列表，然后：

若当前积累向量不足 3 个，直接返回 None（不切割）。

若达到 3 个以上，对整个 `_vecs` 数组计算 `block_similarities`（窗口大小 2 的滑动块间余弦相似度），再用 `depth_scores` 计算 TextTiling 深度分（每个 gap 处的左峰值和右峰值之差之和），再用 `_confirm_peaks` 寻找在连续下降次数 ≥ 2 后被确认的极值点。

若找到深度超过 `min_depth`（默认 0.01）的确认峰值，选取深度最大的那一个作为切割点，返回该 gap index。

若无峰值且 buffer 中向量数量超过 `max_beat`（默认 30），触发安全阀逻辑：在当前 `sims` 序列中找余弦相似度最低（即语义距离最大）的 gap，将其作为强制切割点返回。

若 `push` 返回了切割点 `cut`，Conductor 调用 `_flush_segment(cut)`。

---

### 1.6 _flush_segment — 事件写入 Pool

`_flush_segment(gap_index)` 的行为：
1. 从 `StreamGorge` 消费 index 0 到 gap_index 的向量（`sg.consume(gap_index)` 返回这段 numpy 数组，并将这些向量从内部 buffer 移除）。
2. 从 `_beat_buf` 取出相应的 beat 对象，清空已消费部分。
3. 将这批 beat 的 `text` 字段拼接为纯文本（换行分隔），作为事件正文。
4. 对这批向量取均值，归一化为 float32，作为事件的 fingerprint（特征向量）。
5. 取第一个 beat 的时间戳作为事件时间。
6. 生成 event_id（格式如 `ev_MMDD_序号_哈希`）。
7. 调用 `pool.ingest_event(event_id, t, body, fingerprint)`，将数据写入 Pool 的各层（见第二节）。
8. 调用 `_post_ingest([event_id])`，触发图构建（见第四节）。
9. 返回 event_id。

---

## 二、Pool — 分层存储

每次 `pool.ingest_event(event_id, t, body, fp)` 发生以下写入：

**Layer 1 — 内容层**：将 `body` 写入 `pool/events/<event_id>.md`，纯文本文件。

**Layer 2 — 元数据层**：创建一个 `Event` 数据类实例（含 `id`、`t`、`access_count=0`、`fingerprint_idx`），追加序列化 JSON 到 `pool/events.jsonl`。`fingerprint_idx` 为该 event 在 fingerprints 矩阵中的行索引，等于当前矩阵的行数（新行追加）。

**Layer 3 — 指纹层**：将 fingerprint 向量（bge-m3 默认 1024 维 float32）追加到 `pool/fingerprints.npy` 矩阵，新矩阵维度为 `(N+1, 1024)`。

**Layer 4 — 余弦层**（在 `_update_cosine` 中延迟计算）：对 fingerprints 矩阵中所有已有向量重新计算 `(N+1, N+1)` 的全对称余弦相似度矩阵，写入 `pool/cosine.npy`。这是一次 O(N²) 操作，每次 ingest 都会发生。

**Layer 5 — 图边层**：`edge_index.npy`（[2, E] int64）和 `edge_attr.npy`（[E, D] float32）存储 PyG 格式的图边，由 `graph_builder` 写入，不在 `ingest_event` 内发生。

---

## 三、Recall 刷新路径（auto 模式，漂移驱动）

当 `detect_drift` 触发时，`_refresh_recall(query_vec)` 被调用（在 Conductor 处理 beat 的线程内，即 daemon 主线程）。

**步骤 1 — 种子激活**（`seed_activation`）：将 `query_vec` 与 `fingerprints.npy` 矩阵中所有行向量逐一做余弦相似度，得到形状为 `(N,)` 的激活向量。今天（UTC 0 点之后）创建的 event 的激活值被强制置 0（shielding，防止刚写入的事件被立即召回）。

**步骤 2 — 激活传播**（`spread_activation`）：在 Pool 图的边上多步传播能量，步数 `steps=2`，每跳乘衰减系数 `decay=0.5`，边类型乘系数（causal 1.4 最高，contrast 0.3 最低，temporal 0.5），收敛后激活向量归一化到 [0, 1]。每个节点仅在其首次被激活超过阈值时传播（fire-once 语义）。

**步骤 3 — 采样与写文件**（`retrieve` 函数）：将最终激活值作为 Bernoulli 概率，对每个节点独立采样，选出激活值大于 0.4 的事件，最多保留 `top_k=3` 个。

对每个命中的 event，调用 `pool.read_body(event_id)` 读取 `.md` 文件内容，截取前 200 字符（超出则加 `...`），计算时间距今的语言描述（"刚才"/"X 小时前"/"X 天前"/"X 个月前"）。

结果拼接为 Markdown 格式写入 `recall.md`，首行为 HTML 注释 `<!-- recall | ISO时间 -->`，之后每条事件一行（`- (时间描述) 片段文本`）。

同时在 `recall.md` 同目录创建 `.recall_dirty` 空文件作为脏标记。

被命中的每个 Event 对象的 `access_count` 加 1，随后 `pool.save_events()` 将内存中的 events 列表重新序列化写回 `events.jsonl`（全量重写，不是追加）。

---

## 四、图边构建（auto 模式 _post_ingest 触发）

每次 `_flush_segment` 后，`_post_ingest([event_id])` 调用 `build_edges(pool, event_ids, config)`。

**时序边**：遍历 Pool 中所有 events，按时间排序后，在相邻两个 event 之间（至少有一个是新 event）检查时间间隔。若间隔 ≤ 600 秒（10 分钟），创建类型为 `temporal`（type_id=0）的有向边，权重 = `max(0.1, 1 - gap / 600)`。

**语义边**：读取 `cosine.npy`，对每对 event（至少有一个是新的），若余弦相似度 > 0.82，创建类型为 `semantic`（type_id=1）的边，权重 = 该相似度值。

**LLM 命名边**：将新 event 的 body 与相邻若干 event 的 body 拼入 prompt，调用 `[graph]` 配置中的 DeepSeek-compatible API（`edge_base_url` / `edge_model` / `edge_api_key_env`）。要求 LLM 返回 JSON 格式的边列表，每条边含 `src`、`dst`、`type`（causal/remind/contrast/elaboration/semantic/temporal）、`weight`、`reason`。manual 标注路径还会要求返回 `names`，用于生成事件文件名。

所有边统一追加写入 `edge_index.npy` 和 `edge_attr.npy`，以 `numpy.concatenate` 方式扩展矩阵。

---

## 四点五、Manual 标注路径（默认数据收集期）

Dashboard 通过 `/api/annotate` 读取 `flow.jsonl` 中尚未处理的 beat。后端读取 `store/annotation_state.json` 的 `processed_until`，若请求 offset 早于该值会自动夹到 processed_until，防止已确认区间被重新标注。

前端展示 beat 列表，人工在 gap 上标两类切点：
- `event_cut`：事件边界，用于最终切分 Pool event。
- `drift_cut`：话题漂移边界，用于训练漂移检测，不一定等同事件边界。

点击批量处理后，前端把人工 cuts 发送到 `/api/annotate/edges`。此时后端才调用 `annotator.propose_edges()`，使用 `[graph]` 中的 DeepSeek-compatible 配置（默认 `FIAM_GRAPH_API_KEY`）返回 `names` 与 `edges`。

确认时 `/api/annotate/confirm` 会：
1. 用人工 `event_cut` 组合 beat 片段。
2. 优先从 `FeatureStore` 读取冻结 beat 向量，缺失时才 fallback 到现算 embedding。
3. 对每段取均值并归一化，写入 Pool。
4. 保存训练数据：`flow_cut_labels.jsonl`、`beat_boundaries.jsonl`、`event_similarities.jsonl` 和对应 npy。
5. 对相邻事件在 30 分钟内建立弱 temporal 边；DeepSeek 对同 pair 给出的边覆盖弱边。
6. 更新 `annotation_state.processed_until` 锁定处理区间。

---

## 五、CC 会话数据入站流（JSONL 解析路径）

daemon 主循环在检测到 `~/.claude/projects/<sanitized_home>/` 目录下任意 `.jsonl` 文件的 mtime 变化后，调用 `_ingest_new_beats()`。

`_ingest_new_beats` 加载游标文件（记录每个 JSONL 文件上次处理的 byte offset），对 mtime 有变化的文件，调用 `conductor.receive_cc(jsonl_path, byte_offset)`。

`receive_cc` 实例化 `ClaudeCodeAdapter`，调用 `adapter.parse_beats(jsonl_path, byte_offset, user_status, ai_status)`。

`ClaudeCodeAdapter` 从 `byte_offset` 处以字节方式读取文件剩余内容，逐行 JSON 解析：

- **type=user**：提取 `message.content`（字符串），若通过系统标签过滤（`_SYSTEM_TAG_RE` 匹配的 XML 前缀则跳过），构造 `{"role": "user", "text": ..., "timestamp": ...}` 收入 `user_turns`，同时记录 `uuid → index` 映射。
  
- **type=attachment**（type 为 hook_additional_context）：提取 `content` 列表拼接为字符串，通过 `_extract_inbox_from_attachment` 仅取 `[external]` 节（用正则匹配该节内容，`[recall]` 节被丢弃不入事件图），挂载到对应 `parentUuid` 的 user turn 上作为 `inbox_context` 字段。

- **type=assistant**：提取 content 数组中 type 为 `text` 的块和 type 为 `thinking` 的块。同一 `message.id` 可能出现多次（CC 分多次 emit 同一条消息的 partial + final），后出现的 text 覆盖前者，thinking 则追加合并。仅收录 text 或 thinking 非空的条目。

解析完毕后，`parse_beats` 将每个 turn 转换为 `Beat` 对象：普通 user/assistant 正文 source 设为 `"cc"`；tool_use 块生成 source 为 `"action"` 的 Beat；出站路由标记 `[→target:Name]` 生成 source 为 `"dispatch"` 的 Beat，正文放 `text`，目标与收件人放 `meta.target` / `meta.recipient`。hook attachment 在 beat 模式下整体跳过，避免 `[self]` / `[recall]` / `[external]` 通过 CC JSONL 二次进入 flow。

每个 Beat 经 `Conductor._ingest_beat` 处理，流程与第一节相同（写 flow.jsonl → embed → drift check → gorge → 可能 flush 事件）。

新的 byte offset 写回游标文件，供下次轮询继续。

---

## 六、唤醒 CC 路径（_wake_session）

当 daemon 决定唤醒 CC 时，调用 `_wake_session(config, message, tag, conductor)`。

构造命令行数组：`["claude", "-p", "[wake:<tag>] <message>", "--output-format", "json", "--max-turns", "10"]`，可选追加 `--model`、`--disallowedTools`，若有 active session 则追加 `--resume <session_id>`。

用 `subprocess.run` 执行，`cwd` 设为 `config.home_path`，`timeout=180`，捕获 stdout 和 stderr。

stdout 被当作 JSON 解析，结构为 `{session_id, result, total_cost_usd, num_turns, subtype}`。

若解析成功且 `total_cost_usd > 0`，调用 `log_cost` 写入 `logs/cost.jsonl`。

从 `data["result"]`（CC 本轮最终回复文本）中：

- 用 `_OUTBOUND_RE` 正则提取 `[→target:recipient]` 出站消息标记，每个匹配得到 `(channel, recipient, body)` 三元组，调用 `conductor.dispatch(channel, recipient, body)`（走 MQTT bus 发布到 `fiam/dispatch/<target>`）。

- 调用 `extract_later_todos` 提取 `<later at="..." reason="..." />` 格式的稍后处理标记，批量追加到 `todo.jsonl`。

- 调用 `extract_state_tag` 提取最后一个 `<sleep>` / `<mute>` / `<notify>` 状态标记（兼容旧 SLEEP）。若为 sleep，将 `state=sleep`、`until` 和 `reason` 写入 `self/ai_state.json`，同时将 active session 归档到 `self/retired/<ts>_sleep.json` 并清空 `active_session.json`。旧 `self/sleep_state.json` 会在读取时自动迁移。

若 `data["subtype"] == "error_max_turns"`，视为部分成功（session_id 仍然有效），记录日志但不清除 session。

若进程退出码非 0 且非 error_max_turns，记录失败，若是新 session 但仍有 session_id，保存该 ID（防止下次再新建）。

若 `--resume` 未被使用（新 session），从响应中取 `session_id` 写入 `active_session.json`。

---

## 七、hook 注入路径（inject.sh）

CC 触发 `UserPromptSubmit` hook 时，运行 `inject.sh`，其 `CLAUDE_PROJECT_DIR` 环境变量被 CC 设置为当前项目目录（即 `config.home_path`）。

inject.sh 的拼装逻辑：

**Section 1 — self**：遍历 `$HOME_DIR/self/*.md`（不递归），将每个非空 .md 文件内容以 `# <文件名去扩展名>` 为标题拼接，组成 `[self]\n<内容>` 块。

**Section 2 — recall**：仅当 `.recall_dirty` 文件存在且 `recall.md` 非空时才注入。将 `recall.md` 内容用 `sed` 去掉 HTML 注释行，压缩连续空行，组成 `[recall]\n<内容>` 块。读取后删除 `.recall_dirty` 标记文件。

**Section 3 — external（pending_external.txt）**：若文件非空，先用 `mv` 原子性重命名为 `.processing`，再读取内容，组成 `[external]\n<内容>` 块，最后移动归档到 `inbox/processed/external_<时间戳>.txt`。

三个 section 拼接后，用 Python 做 JSON 字符串转义（`json.dumps` 后去掉首尾引号），输出 `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"<转义内容>"}}` 到 stdout，CC 以此将内容注入对话上下文。

CC 将注入的 additionalContext 以 `type=attachment` 行写入对应 user turn 的后面的 JSONL 行。adapter 在解析时只提取 `[external]` 节，`[recall]` 和 `[self]` 不进入事件图。

---

## 八、出站消息路径

出站消息有两条来源：

**来源 A — _wake_session 内直接 dispatch**：CC 回复中的 `[→target:recipient]` 标记，由 `_extract_and_dispatch` 解析后调用 `conductor.dispatch(channel, recipient, body)`，发布到 `fiam/dispatch/<target>` MQTT topic。

**来源 B — outbox.sh hook + sweep_outbox**：CC 的 `Stop` hook 运行 `outbox.sh`，从 stdin 读取 CC 传入的 JSON（含 `stopHookData.transcript`），提取最后一条 assistant 消息文本，用 Python 正则找出 `[→email:recipient]` 块，每个匹配写入 `outbox/<timestamp>.md` 文件，frontmatter 含 `to`、`via=email`、`priority`。  
daemon 主循环每轮调用 `sweep_outbox(config)`，扫描 `outbox/*.md`，解析 frontmatter，取 `via` 决定发送方式，实际发送完成后将文件移到 `outbox/sent/`。`sweep_outbox` 直接调用 postman 的底层发送函数，不经过 MQTT bus。

**bridge 侧接收 dispatch**：`bridge_email.py` 订阅 `fiam/dispatch/email`，回调接收 payload，取 `text` 和 `recipient`。正文通过 `postman._email_send()` 发送到 SMTP。

---

## 九、todo 队列路径

daemon 主循环每轮调用 `archive_stale(config)`，清理 `todo.jsonl` 中已过期（超过 grace 窗口）或超过最大尝试次数的条目，移入 `todo_missed.jsonl` / `todo_failed.jsonl`。

随后调用 `load_due(config)`，返回所有 `at` ≤ 当前 UTC 时间且状态为 pending 的条目。

对每条 due 条目，先检查 budget，若超预算则调用 `mark_done(entry, success=False)` 不实际继续。否则检查 `ai_state`；若 sleep 为 `"open"`，先清除 sleep 状态再继续；若 sleep 为明确时间点且当前时间未过，调用 `mark_done(entry, success=True)` 跳过（AI 的主动休眠优先于稍后任务）。

通过 budget 和 sleep 检查后，调用 `_wake_session(config, "[todo:<type>] <reason>", tag="todo", conductor=_conductor)`，流程与第六节完全相同。

---

## 十、状态持久化文件一览

| 文件 | 读取方 | 写入方 | 内容 |
|---|---|---|---|
| `flow.jsonl` | 归档/重建 | Conductor._ingest_beat | 每条 beat 的 JSON 行；切分/向量只读 `text` |
| `features/chunks/flow_vectors_*.npy` | annotator, 训练脚本 | FeatureStore | 冻结 beat 向量分片矩阵 |
| `features/flow_index.jsonl` | annotator, 训练脚本 | FeatureStore | beat hash 到向量分片/行号的索引 |
| `annotation_state.json` | dashboard annotate | dashboard confirm | 已确认 flow 区间锁 |
| `pool/events.jsonl` | Pool | Pool.ingest_event, Pool.save_events | 事件元数据列表（全量重写） |
| `pool/events/*.md` | Pool, recall | Pool.ingest_event | 事件正文文本 |
| `pool/fingerprints.npy` | Pool, spread | Pool.ingest_event | 事件向量矩阵 |
| `pool/cosine.npy` | Pool, graph_builder | Pool.ingest_event | 事件间余弦相似度矩阵 |
| `pool/edge_index.npy` / `edge_attr.npy` | spread_activation | graph_builder | 图边 PyG 格式 |
| `recall.md` | inject.sh | _refresh_recall | 最新联想片段 |
| `.recall_dirty` | inject.sh | _refresh_recall | 脏标记（存在即有新 recall） |
| `pending_external.txt` | inject.sh, _wake_session | _write_pending_external | 待注入外部消息文本 |
| `active_session.json` | daemon | _save_active_session | 当前 CC session_id + 时间戳 |
| `self/ai_state.json` | daemon | _save_ai_state / _save_sleep_state | notify / mute / block / sleep / busy / together；可带 until / expires_at / reason |
| `self/todo.jsonl` | todo | append_to_todo | 稍后任务队列 |
| `interactive.lock` | _is_interactive | outbox.sh（清除） | 交互锁（含 pid） |
| `logs/pipeline.log` | 人工查阅 | daemon _plog | 流水线日志 |
| `logs/daemon_state.json` | dashboard | _write_daemon_state | 当前 daemon 快照 |
| `logs/cost.jsonl` | check_budget | log_cost | 每次唤醒费用记录 |
| `cursor.json` | daemon | _save_cursor | 每个 JSONL 文件的 byte offset |
