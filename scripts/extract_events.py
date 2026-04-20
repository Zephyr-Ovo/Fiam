"""从 ISP 拉取所有事件 + graph.jsonl 到本地 training_data/。

用法：python scripts/extract_events.py
前提：ssh isp 可连（通过 ProxyJump relay）

输出：
  training_data/events/*.md     — 所有事件原文件
  training_data/graph.jsonl     — 图边
  training_data/events_text.jsonl — 纯文本索引 {id, text, source}
"""

import subprocess, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "training_data"
EVENTS_DIR = OUT / "events"
EVENTS_DIR.mkdir(parents=True, exist_ok=True)

SSH = "ssh"
HOST = "isp"
REMOTE_STORE = "~/fiam-code/store"
REMOTE_POOL = "~/fiam-code/store/pool"


def ssh_run(cmd: str) -> str:
    r = subprocess.run([SSH, HOST, cmd], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"SSH error: {r.stderr.strip()}", file=sys.stderr)
    return r.stdout


def scp_download(remote_path: str, local_path: str):
    subprocess.run(["scp", "-q", f"{HOST}:{remote_path}", local_path],
                   check=True, timeout=60)


def strip_frontmatter(md_text: str) -> str:
    """去掉 YAML frontmatter，返回纯内容。"""
    if md_text.startswith("---"):
        end = md_text.find("---", 3)
        if end != -1:
            return md_text[end + 3:].strip()
    return md_text.strip()


def event_id_from_filename(fname: str) -> str:
    return fname.removesuffix(".md")


def main():
    print("=== Step 1: 列出 ISP 事件文件 ===")

    # store/events/
    named_list = ssh_run(f"ls {REMOTE_STORE}/events/*.md 2>/dev/null").strip().split("\n")
    named_list = [f.strip() for f in named_list if f.strip()]
    print(f"  store/events/: {len(named_list)} 文件")

    # store/pool/events/
    pool_list = ssh_run(f"ls {REMOTE_POOL}/events/*.md 2>/dev/null").strip().split("\n")
    pool_list = [f.strip() for f in pool_list if f.strip()]
    print(f"  store/pool/events/: {len(pool_list)} 文件")

    all_remote = named_list + pool_list
    print(f"  总计: {len(all_remote)} 个事件")

    print("\n=== Step 2: 下载事件文件 ===")
    downloaded = 0
    for remote_path in all_remote:
        fname = Path(remote_path).name
        local_path = EVENTS_DIR / fname
        if local_path.exists():
            continue
        try:
            scp_download(remote_path, str(local_path))
            downloaded += 1
        except subprocess.CalledProcessError as e:
            print(f"  跳过 {fname}: {e}", file=sys.stderr)
    print(f"  新下载 {downloaded}, 已有 {len(all_remote) - downloaded}")

    print("\n=== Step 3: 下载 graph.jsonl ===")
    graph_local = OUT / "graph.jsonl"
    scp_download(f"{REMOTE_STORE}/graph.jsonl", str(graph_local))
    edges = sum(1 for _ in open(graph_local))
    print(f"  {edges} 条边")

    print("\n=== Step 4: 构建纯文本索引 ===")
    index = []
    for md_file in sorted(EVENTS_DIR.glob("*.md")):
        eid = event_id_from_filename(md_file.name)
        text = strip_frontmatter(md_file.read_text(encoding="utf-8"))
        if not text or len(text) < 10:
            continue
        # 判断来源：pool 文件名格式 MMDD_HHMM_xxxx
        source = "pool" if re.match(r"\d{4}_\d{4}_", md_file.name) else "named"
        index.append({"id": eid, "text": text, "source": source})

    index_path = OUT / "events_text.jsonl"
    with open(index_path, "w", encoding="utf-8") as f:
        for item in index:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"  {len(index)} 个有效事件写入 {index_path.name}")
    print(f"  named: {sum(1 for i in index if i['source']=='named')}")
    print(f"  pool:  {sum(1 for i in index if i['source']=='pool')}")

    # 统计文本长度
    lengths = [len(i["text"]) for i in index]
    lengths.sort()
    if lengths:
        print(f"  文本长度: min={lengths[0]}, median={lengths[len(lengths)//2]}, max={lengths[-1]}")


if __name__ == "__main__":
    main()
