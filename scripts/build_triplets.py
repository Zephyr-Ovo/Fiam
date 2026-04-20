"""从 graph.jsonl + events_text.jsonl 自动构造排序三元组。

输入：
  training_data/graph.jsonl       — 图边 {src, dst, type, weight}
  training_data/events_text.jsonl — 事件纯文本 {id, text, source}

输出：
  training_data/triplets_v0.jsonl — {anchor_id, pos_id, neg_id, anchor, pos, neg, edge_type, edge_weight}

策略：
  - 每条有权重的边 (A→B) = 正对
  - 从剩余事件中随机选 C（和 A 无直接边） = 负
  - 每条边生成 2 个三元组（不同负样本），增加多样性
  - 过滤掉过短事件（< 30 字）
"""

import json, random, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "training_data"

# 边类型权重映射：语义/因果关系权重高，时间关系低
EDGE_SCORE = {
    "semantic": 0.9,
    "cause": 0.85,
    "remind": 0.8,
    "elaboration": 0.75,
    "contrast": 0.6,    # 对比也是一种关联
    "temporal": 0.5,     # 时间相邻未必语义相关
}

MIN_TEXT_LEN = 30
NEGS_PER_EDGE = 2
SEED = 42


def load_events(path: Path) -> dict[str, str]:
    """返回 {event_id: text}，过滤短文本。"""
    events = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            if len(item["text"]) >= MIN_TEXT_LEN:
                events[item["id"]] = item["text"]
    return events


def load_graph(path: Path) -> list[dict]:
    """返回边列表。"""
    edges = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            edges.append(json.loads(line))
    return edges


def build_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    """构建邻接表（双向）。"""
    adj = defaultdict(set)
    for e in edges:
        adj[e["src"]].add(e["dst"])
        adj[e["dst"]].add(e["src"])
    return adj


def truncate(text: str, max_chars: int = 512) -> str:
    """截断长文本用于训练（bge-m3 也截断到 512）。"""
    return text[:max_chars] if len(text) > max_chars else text


def main():
    events_path = DATA / "events_text.jsonl"
    graph_path = DATA / "graph.jsonl"

    if not events_path.exists():
        print("先跑 extract_events.py 下载事件", file=sys.stderr)
        sys.exit(1)

    events = load_events(events_path)
    edges = load_graph(graph_path)
    adj = build_adjacency(edges)

    all_ids = list(events.keys())
    rng = random.Random(SEED)

    print(f"有效事件: {len(events)}")
    print(f"图边: {len(edges)}")
    print(f"有事件文本的边: {sum(1 for e in edges if e['src'] in events and e['dst'] in events)}")

    triplets = []
    skipped = 0

    for edge in edges:
        src, dst = edge["src"], edge["dst"]
        if src not in events or dst not in events:
            skipped += 1
            continue

        # 找不和 anchor 直接相连的事件作负样本
        neighbors = adj[src]
        neg_pool = [eid for eid in all_ids if eid != src and eid != dst and eid not in neighbors]

        if not neg_pool:
            skipped += 1
            continue

        for _ in range(NEGS_PER_EDGE):
            neg_id = rng.choice(neg_pool)
            score = EDGE_SCORE.get(edge["type"], 0.5)

            triplets.append({
                "anchor_id": src,
                "pos_id": dst,
                "neg_id": neg_id,
                "anchor": truncate(events[src]),
                "pos": truncate(events[dst]),
                "neg": truncate(events[neg_id]),
                "edge_type": edge["type"],
                "edge_weight": edge["weight"],
                "score": score,
            })

    # 去重（同一 anchor+pos+neg 组合）
    seen = set()
    unique = []
    for t in triplets:
        key = (t["anchor_id"], t["pos_id"], t["neg_id"])
        if key not in seen:
            seen.add(key)
            unique.append(t)

    out_path = DATA / "triplets_v0.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for t in unique:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"\n结果:")
    print(f"  生成三元组: {len(unique)} (去重前 {len(triplets)})")
    print(f"  跳过（缺文本/无负样本）: {skipped}")
    print(f"  输出: {out_path}")

    # 按边类型统计
    by_type = defaultdict(int)
    for t in unique:
        by_type[t["edge_type"]] += 1
    print(f"\n  按边类型分布:")
    for etype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {etype}: {count}")


if __name__ == "__main__":
    main()
