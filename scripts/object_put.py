from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SRC_PATH = str(SRC)
if SRC_PATH in sys.path:
    sys.path.remove(SRC_PATH)
sys.path.insert(0, SRC_PATH)

from fiam.config import FiamConfig  # noqa: E402
from fiam.store.objects import ObjectStore  # noqa: E402


def _resolve_home_file(home: Path, rel: str) -> Path:
    path = (home / rel).resolve()
    root = home.resolve()
    if path != root and root not in path.parents:
        raise ValueError("path must stay inside home_path")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a home file into ObjectStore and print an obj token.")
    parser.add_argument("--config", default=str(ROOT / "fiam.toml"), help="Path to fiam.toml")
    parser.add_argument("--path", required=True, help="File path relative to home_path")
    parser.add_argument("--name", default="", help="Attachment filename override")
    parser.add_argument("--mime", default="", help="MIME type override")
    parser.add_argument("--summary", default="", help="Short searchable summary")
    parser.add_argument("--direction", default="outbound", choices=["outbound", "generated", "inbound"])
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = FiamConfig.from_toml(config_path, ROOT)
    source = _resolve_home_file(config.home_path, args.path)
    data = source.read_bytes()
    name = Path(args.name or source.name).name
    mime = args.mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    object_hash = ObjectStore(config.object_dir).put_bytes(data, suffix="")

    manifest = config.home_path / "uploads" / "manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "sha256": object_hash,
        "object_hash": object_hash,
        "name": name,
        "mime": mime,
        "size": len(data),
        "direction": args.direction,
        "source": "object_put.py",
    }
    if args.summary:
        record["summary"] = args.summary
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(json.dumps({"ok": True, "token": f"obj:{object_hash[:12]}", **record}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())