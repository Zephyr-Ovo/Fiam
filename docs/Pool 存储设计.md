# Pool 存储设计

> fiam 的 5 层统一存储，取代旧架构中散落在四处的文件。

## 旧架构的问题

旧的 store 结构：
```
store/
  events/          81 个 .md 文件（YAML frontmatter + body）
  embeddings/      81 个 .npy 文件（一一对应，1024-dim）
  graph.jsonl      边列表 {src, dst, type, weight}
  graph/           DS 命名后的 .md（和 events/ 重复！）
```

问题：
- **分散** — 事件、嵌入、边存在四个不同地方
- **冗余** — graph/ 和 events/ 内容重复
- **检索低效** — 每次查询遍历所有 .npy 文件逐个 load + cosine
- **无预计算** — 没有两两相似度矩阵，新事件入池无法快速找候选
- **格式繁重** — EventRecord 有十几个字段（intensity、strength、user_weight、last_accessed...）

## Pool 5 层设计

Session 14 讨论中，Zephyr 确定了分层原则：**只存原始信息，其他都计算**。

```
pool/
  events/              内容层 — 每个事件一个 .md 文件
  events.jsonl         元数据层 — {id, t, access_count, fingerprint_idx}
  fingerprints.npy     指纹层 — N × 1024 矩阵
  cosine.npy           相似度层 — N × N 矩阵
  edge_index.npy       关系层 — [2, E] 边索引
  edge_attr.npy        关系层 — [E, D] 边属性
```

### 层 1：内容

`pool/events/<event_id>.md` — 纯文本，没有 YAML frontmatter。每个文件就是事件的 body。

便于 console 直接编辑、便于 grep 搜索。event_id 由 DeepSeek 命名（英文 snake_case，有意义）。

### 层 2：元数据

`pool/events.jsonl` — 每行一个事件的元信息。

```json
{"id": "morning_architecture_discussion", "t": "2026-04-19T02:00:00+00:00", "access_count": 3, "fingerprint_idx": 42}
```

**极简四字段**：
- `id` — 事件标识（DS 命名）
- `t` — 时间戳（第一个 beat 的时间）
- `access_count` — 被检索/访问次数（每次 recall 时 +1）
- `fingerprint_idx` — 指向 fingerprints.npy 的行号

去掉了旧架构的 intensity、strength、user_weight、last_accessed、embedding path、embedding_dim、links。Zephyr 的理由：

> "权重在 edge 上不在节点上。节点的'重要性'由从 seed 连乘来的边权决定。"

### 层 3：指纹

`pool/fingerprints.npy` — shape `(N, 1024)`，float32。

每行对应一个事件的语义指纹（所有组成 beat 的 embedding 均值）。bge-m3 输出 1024 维。

新事件入池 → 追加一行。重索引时整个矩阵重建。

### 层 4：相似度

`pool/cosine.npy` — shape `(N, N)`，float32。

两两余弦相似度矩阵。**主要用于建边候选快速查找**，不直接用于检索。

新事件入池 → 加一行一列（只需算新事件 vs 所有旧事件，O(N) 而非 O(N²)）。

### 层 5：关系（边）

PyG 兼容格式（未来可直接喂给 GNN）：
- `edge_index.npy` — shape `[2, E]`，int64。每列是一条有向边 (src, dst)
- `edge_attr.npy` — shape `[E, D]`，float32。边属性（类型 one-hot + 权重）

边类型详见 [[记忆图谱与检索#边的类型]]。

## Pool 类接口

```python
# src/fiam/store/pool.py
class Pool:
    def __init__(self, pool_dir: Path, dim: int = 1024): ...
    
    def ingest_event(self, event_id, t, body, fingerprint): ...
    def delete_event(self, event_id): ...     # full cascade
    def rename_event(self, old_id, new_id): ...
    
    def load_events(self) -> list[EventRecord]: ...
    def save_events(self): ...
    def get_event(self, event_id) -> EventRecord | None: ...
    def read_body(self, event_id) -> str: ...
    
    def load_fingerprints(self) -> np.ndarray: ...
    def load_cosine(self) -> np.ndarray: ...
    def load_edges(self) -> tuple[np.ndarray, np.ndarray]: ...
```

### 级联删除

`delete_event()` 的完整级联：
1. 删除相关的边（edge_index + edge_attr 中引用该索引的行）
2. 所有大于该索引的边索引 -1（shift）
3. 从 fingerprints.npy 删除对应行
4. 从 cosine.npy 删除对应行和列
5. 更新所有 `fingerprint_idx > 删除索引` 的事件（-1）
6. 从 events.jsonl 删除记录
7. 删除 body .md 文件

这保证了所有层的一致性。

> 📁 `src/fiam/store/pool.py` — Pool 类

## 与 Console 的交互

Zephyr 对 console 编辑能力有很高要求：

> "看到叙事流原文，进入 graph，悬停展示指纹，点击进入内容查看。修改内容 → 语义向量更新 → 所有相似度更新。事件可以合并。边可以编辑类型、权重。"

当前已实现：
- 事件查看/编辑/删除（通过 dashboard API）
- 图节点悬停 → 显示事件名
- 边右键 → 编辑类型/权重
- flow 页面查看 beat 流

待实现：
- 编辑 body → 自动 re-embed + 更新 cosine 矩阵
- 事件合并（两个事件 body 拼接 → 新指纹 → 重建边）

---

← 返回 [[构建 fiam 的旅程]] · 相关：[[Conductor 与信息流]] · [[记忆图谱与检索]]
