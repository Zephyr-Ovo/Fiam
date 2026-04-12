"""
Awareness context builder — generates the environment snapshot
injected alongside recall.md before every session.

This is NOT a file the AI reads manually. It's automatically prepended
to the hook's additionalContext so the AI "sees" it the moment it wakes.

Produces a Markdown block covering:
  - Current time and location
  - Trigger source (user message / self-scheduled / system)
  - Unread messages (inbox/)
  - Schedule queue status
  - Environment map (where the AI is, how to move)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fiam.config import FiamConfig


def _count_inbox(config: FiamConfig) -> list[str]:
    """Summarize unread inbox messages."""
    inbox = config.inbox_dir
    if not inbox.exists():
        return []
    files = sorted(inbox.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return []
    lines = [f"未读消息: {len(files)} 条"]
    for f in files[:5]:
        try:
            import frontmatter as fm
            post = fm.load(str(f))
            sender = post.metadata.get("from", "unknown")
            via = post.metadata.get("via", "?")
            ts = post.metadata.get("time", "?")
            preview = post.content.strip()[:80].replace("\n", " ")
            lines.append(f"  - [{via}] {sender} ({ts}): {preview}")
        except Exception:
            lines.append(f"  - {f.name}")
    if len(files) > 5:
        lines.append(f"  ...及 {len(files) - 5} 条更多")
    return lines


def _schedule_summary(config: FiamConfig) -> list[str]:
    """Get pending schedule entries summary."""
    # Import here to avoid circular dependency
    from fiam_lib.scheduler import queue_summary
    return queue_summary(config).splitlines()


def _wake_note(config: FiamConfig) -> str | None:
    """Read wake note if this session was self-triggered."""
    note_path = config.home_path / ".wake_note"
    if note_path.exists():
        return note_path.read_text(encoding="utf-8").strip()
    return None


def _detect_node() -> str:
    """Detect which node we're running on by checking hostname/IP."""
    import socket
    try:
        hostname = socket.gethostname().lower()
        # Try to get external-facing IP hints from hostname or known patterns
        if any(tag in hostname for tag in ("isp", "vps", "ubuntu")):
            return "isp"
        if any(tag in hostname for tag in ("do-", "droplet")):
            return "do"
        # Windows hostnames typically indicate local
        if any(tag in hostname for tag in ("desktop", "laptop", "win", "pc")):
            return "local"
    except Exception:
        pass
    # Fallback: check if common ISP paths exist
    from pathlib import Path
    if Path("/root/fiam-code").exists() or Path("/home/fiam/fiam-code").exists():
        return "isp"
    if Path("F:/fiam-code").exists() or Path("C:/Users").exists():
        return "local"
    return "unknown"


def _check_tunnel_alive() -> bool:
    """Check if the reverse tunnel to Local is reachable (ISP:2222)."""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 2222), timeout=2)
        s.close()
        return True
    except (OSError, socket.timeout):
        return False


def _env_map() -> list[str]:
    """Dynamic environment map — where the AI is and where it can go."""
    node = _detect_node()
    tunnel_up = _check_tunnel_alive() if node == "isp" else False

    lines = ["环境地图:"]

    if node == "isp":
        lines.append("  - [当前] ISP (99.173.22.93) — CC 全功能，SSH 可用，主站")
        lines.append("  - DO (209.38.69.231) — ~/tools/go_do.sh — 算力节点 (embedding API)")
        local_status = "隧道通畅" if tunnel_up else "隧道断开 — Local 不可达，降级为 TG"
        lines.append(f"  - 本地 (Zephyr 电脑) — ~/tools/go_local.sh — {local_status}")
    elif node == "do":
        lines.append("  - [当前] DO (209.38.69.231) — 算力节点，无状态")
        lines.append("  - ISP (99.173.22.93) — ~/tools/return_isp.sh — 回主站")
    elif node == "local":
        lines.append("  - [当前] Local (Zephyr 电脑) — Desktop/AW 数据可用")
        lines.append("  - ISP (99.173.22.93) — 主站 (通过 relay 中转)")
    else:
        lines.append(f"  - [当前] 未知节点 ({node})")

    lines.append("  - Telegram — outbox/ via:telegram 发送 | inbox/ 接收")
    lines.append("  - Email — outbox/ via:email 发送 (fiet@fiet.cc via Zoho)")

    return lines


def build_awareness(config: FiamConfig) -> str:
    """Build the full awareness context string for hook injection."""
    from zoneinfo import ZoneInfo

    pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    utc = datetime.now(timezone.utc)

    sections: list[str] = []

    # -- Header
    sections.append("<!-- awareness: auto-generated, do not edit -->")
    sections.append(f"当前时间: {pt.strftime('%Y-%m-%d %H:%M')} PT ({utc.strftime('%H:%M')} UTC)")

    # -- Trigger source
    wake_note = _wake_note(config)
    if wake_note:
        sections.append(f"唤醒方式: 自主调度")
        sections.append(wake_note)
    else:
        sections.append("唤醒方式: 用户消息")

    sections.append("")

    # -- Inbox
    inbox_lines = _count_inbox(config)
    if inbox_lines:
        sections.extend(inbox_lines)
    else:
        sections.append("未读消息: 无")

    sections.append("")

    # -- Schedule
    try:
        sched_lines = _schedule_summary(config)
        sections.extend(sched_lines)
    except Exception:
        sections.append("调度队列: 无法读取")

    sections.append("")

    # -- Environment map
    sections.extend(_env_map())

    sections.append("")

    # -- Outbox reminder
    sections.append("通信: 写文件到 outbox/ (frontmatter via:telegram|email) 即自动发送")
    sections.append("调度: 在回复中写 <<WAKE:ISO时间:private|notify|seek|check:原因>> 设定下次自主唤醒")
    sections.append("  private=后台静默 | notify=完成后推送用户 | seek=寻找用户 | check=环境检查")

    # -- Queue hint
    try:
        from fiam_lib.scheduler import load_pending
        pending = load_pending(config)
        if len(pending) <= 1:
            sections.append("")
            sections.append("提示: 调度队列即将为空。如不添加新 WAKE，下次活动取决于用户消息或外部触发。")
    except Exception:
        pass

    return "\n".join(sections)
