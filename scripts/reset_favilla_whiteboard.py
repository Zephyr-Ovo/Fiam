from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SRC_PATH = str(SRC)
if SRC_PATH in sys.path:
    sys.path.remove(SRC_PATH)
sys.path.insert(0, SRC_PATH)

from fiam.config import FiamConfig  # noqa: E402


PROMPT_PLACEHOLDERS = (
    "identity.md",
    "impressions.md",
    "lessons.md",
    "commitments.md",
    "personality.md",
    "state.md",
    "goals.md",
    "daily_summary.md",
)


class ResetPlan:
    def __init__(self, *, apply: bool) -> None:
        self.apply = apply
        self.actions: list[str] = []

    def note(self, action: str, path: Path) -> None:
        self.actions.append(f"{action}: {path}")

    def truncate(self, path: Path, *, create: bool = False) -> None:
        if path.exists() or create:
            self.note("truncate", path)
            if self.apply:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

    def unlink(self, path: Path) -> None:
        if path.exists():
            self.note("delete", path)
            if self.apply:
                path.unlink()

    def rmtree(self, path: Path) -> None:
        if path.exists():
            self.note("delete-dir", path)
            if self.apply:
                shutil.rmtree(path)


def reset(config: FiamConfig, plan: ResetPlan) -> None:
    config.ensure_dirs()

    plan.truncate(config.constitution_md_path, create=True)
    plan.truncate(config.pending_recall_path, create=True)
    prompt_paths: set[Path] = set()
    for name in PROMPT_PLACEHOLDERS:
        path = config.self_dir / name
        prompt_paths.add(path)
        plan.truncate(path, create=True)
    for path in sorted(config.self_dir.glob("*.md")):
        if path not in prompt_paths:
            plan.truncate(path)

    for path in sorted((config.home_path / "transcript").glob("*.jsonl")):
        plan.unlink(path)
    for name in (
        "app_cuts.jsonl",
        "route_state.json",
        "session_state.json",
        ".debug_last_assembly.json",
        ".debug_last_context.json",
        ".debug_last_context_api.json",
        ".debug_last_context_cc.json",
    ):
        plan.unlink(config.home_path / name)
    plan.unlink(config.active_session_path)
    plan.unlink(config.ai_state_path)
    plan.unlink(config.todo_path)
    plan.unlink(config.home_path / "uploads" / "manifest.jsonl")

    for path in sorted((config.store_dir / "transcripts").glob("*.jsonl")):
        plan.unlink(path)
    for path in (
        config.flow_path,
        config.store_dir / "flow.jsonl.bak",
        config.held_path,
        config.annotation_state_path,
        config.store_dir / "turn_traces.jsonl",
        config.store_dir / "recall_warmup.jsonl",
        config.store_dir / "memory_jobs.jsonl",
        config.store_dir / "memory_jobs.lock",
    ):
        plan.unlink(path)
    for path in (config.event_db_path, config.event_db_path.with_suffix(config.event_db_path.suffix + "-wal"), config.event_db_path.with_suffix(config.event_db_path.suffix + "-shm")):
        plan.unlink(path)

    for path in (
        config.pool_dir,
        config.feature_dir,
        config.timeline_dir,
        config.object_dir,
        config.store_dir / "queue",
        config.store_dir / "wearable",
    ):
        plan.rmtree(path)

    if plan.apply:
        config.ensure_dirs()
        for name in PROMPT_PLACEHOLDERS:
            (config.self_dir / name).touch()
        config.constitution_md_path.touch()
        config.pending_recall_path.touch()


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset local Favilla/FIAM runtime state to a blank whiteboard.")
    parser.add_argument("--config", default=str(ROOT / "fiam.toml"), help="Path to fiam.toml")
    parser.add_argument("--apply", action="store_true", help="Actually delete/truncate files. Without this, only prints the plan.")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = FiamConfig.from_toml(config_path, ROOT)
    plan = ResetPlan(apply=args.apply)
    reset(config, plan)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode}: {len(plan.actions)} actions")
    for action in plan.actions:
        print(action)
    if not args.apply:
        print("Run again with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())