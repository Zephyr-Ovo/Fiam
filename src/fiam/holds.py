"""Storage helpers for delayed Favilla hold replies."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from fiam.config import FiamConfig


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_hold_id(hold_id: str) -> str:
    clean = _SAFE_ID_RE.sub("_", hold_id.strip()).strip("._")
    if not clean:
        raise ValueError("missing hold id")
    return clean


def holds_dir(config: "FiamConfig") -> Path:
    return config.store_dir / "holds"


def hold_path(config: "FiamConfig", hold_id: str) -> Path:
    return holds_dir(config) / f"{_safe_hold_id(hold_id)}.md"


def create_hold_record(
    config: "FiamConfig",
    *,
    source: str,
    runtime: str,
    user_text: str,
    attachments: list[dict[str, Any]] | None,
    reason: str,
    draft: str,
    at: str = "",
) -> dict[str, Any]:
    created = datetime.now(timezone.utc).isoformat()
    hold_id = f"hold-{int(time.time() * 1000)}-{uuid4().hex[:8]}"
    record: dict[str, Any] = {
        "id": hold_id,
        "created": created,
        "source": source,
        "runtime": runtime,
        "reason": reason,
        "at": at,
        "user_text": user_text,
        "attachments": attachments or [],
        "draft": draft,
    }
    path = hold_path(config, hold_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_hold_markdown(record), encoding="utf-8")
    return {**record, "path": str(path)}


def load_hold_record(config: "FiamConfig", hold_id: str) -> dict[str, Any] | None:
    path = hold_path(config, hold_id)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    record = _parse_hold_markdown(text)
    record.setdefault("id", _safe_hold_id(hold_id))
    record.setdefault("path", str(path))
    return record


def hold_record_from_entry(config: "FiamConfig", entry: dict[str, Any]) -> dict[str, Any]:
    hold_id = str(entry.get("hold_id") or "").strip()
    if hold_id:
        record = load_hold_record(config, hold_id)
        if record is not None:
            return {**entry, **record, "hold_id": hold_id}
    return {
        "hold_id": hold_id,
        "source": str(entry.get("source") or "chat"),
        "runtime": str(entry.get("runtime") or "cc"),
        "reason": str(entry.get("reason") or "continue held Favilla chat reply"),
        "at": str(entry.get("at") or ""),
        "user_text": str(entry.get("user_text") or ""),
        "attachments": entry.get("attachments") if isinstance(entry.get("attachments"), list) else [],
        "draft": str(entry.get("draft") or ""),
        "path": str(entry.get("hold_path") or ""),
    }


def append_final_to_hold(config: "FiamConfig", hold_id: str, final_text: str) -> None:
    if not hold_id:
        return
    path = hold_path(config, hold_id)
    if not path.exists():
        return
    stamp = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as file:
        file.write(f"\n\n## Final ({stamp})\n\n{final_text.strip()}\n")


def _render_hold_markdown(record: dict[str, Any]) -> str:
    meta = {key: value for key, value in record.items() if key not in {"draft"}}
    return (
        "---\n"
        f"{json.dumps(meta, ensure_ascii=False)}\n"
        "---\n\n"
        "# Held Favilla Reply\n\n"
        "## Original User Message\n\n"
        f"{record.get('user_text') or ''}\n\n"
        "## Held Draft\n\n"
        f"{record.get('draft') or ''}\n"
    )


def _parse_hold_markdown(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    record: dict[str, Any] = {}
    if len(lines) >= 3 and lines[0].strip() == "---":
        try:
            record.update(json.loads(lines[1]))
        except (json.JSONDecodeError, TypeError):
            pass
    draft_marker = "## Held Draft"
    if draft_marker in text:
        record["draft"] = text.split(draft_marker, 1)[1].strip()
    return record