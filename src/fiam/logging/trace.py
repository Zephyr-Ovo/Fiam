"""
Pipeline step tracer.

Each pipeline run gets a session directory under logs/sessions/{timestamp}/.
Each step appends one JSON file: {step_name}.json  (or {step_name}_{n}.json if
the same step is called multiple times in a single session).

Extended: also logs embedding stats (shape, max, min, mean, L2 norm) in the
store_write step when an embedding vector is provided.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


class Trace:
    """Records input/output of every pipeline step to a session log directory.

    Usage::

        trace = Trace(logs_root=Path("logs"))

        with trace.step("classifier", inputs={"text": "hello"}) as record:
            result = do_work()
            record["outputs"] = result   # mutate in-place before close

        # Or call directly:
        trace.record("classifier", inputs={...}, outputs={...})
    """

    def __init__(self, logs_root: Path, session_id: str | None = None) -> None:
        if session_id is None:
            session_id = datetime.now(timezone.utc).strftime("%m%d_%H%M")
        self.session_id = session_id
        self.session_dir = Path(logs_root) / "sessions" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._step_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        step_name: str,
        *,
        inputs: Any = None,
        outputs: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Write a single step record and return the path to the written file."""
        count = self._step_counts.get(step_name, 0)
        self._step_counts[step_name] = count + 1

        filename = step_name if count == 0 else f"{step_name}_{count}"
        filepath = self.session_dir / f"{filename}.json"

        entry: dict[str, Any] = {
            "step": step_name,
            "session": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "inputs": _serialise(inputs),
            "outputs": _serialise(outputs),
        }
        if metadata:
            entry["metadata"] = _serialise(metadata)

        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, indent=2, ensure_ascii=False)

        return filepath

    def record_store_write(
        self,
        *,
        event_id: str,
        event_path: str,
        embedding_path: str,
        embedding_vec: np.ndarray | None = None,
        body_preview: str = "",
    ) -> Path:
        """Specialised record for store_write steps — includes embedding stats."""
        outputs: dict[str, Any] = {
            "event_id": event_id,
            "event_path": str(event_path),
            "embedding_path": embedding_path,
            "body_preview": body_preview[:200],
        }

        if embedding_vec is not None:
            outputs["embedding_stats"] = _embedding_stats(embedding_vec)

        return self.record("store_write", outputs=outputs)

    def step(self, step_name: str, *, inputs: Any = None) -> "_StepContext":
        """Context-manager form.  Mutate the returned dict to set outputs.

        Example::

            with trace.step("retriever", inputs=query) as rec:
                rec["outputs"] = retrieve(query)
        """
        return _StepContext(self, step_name, inputs)

    @property
    def session_path(self) -> Path:
        return self.session_dir


# ------------------------------------------------------------------
# Embedding statistics
# ------------------------------------------------------------------

def _embedding_stats(vec: np.ndarray) -> dict[str, Any]:
    """Compute summary statistics for an embedding vector."""
    flat = vec.flatten().astype(float)
    return {
        "shape": list(vec.shape),
        "max": float(np.max(flat)),
        "min": float(np.min(flat)),
        "mean": float(np.mean(flat)),
        "l2_norm": float(np.linalg.norm(flat)),
        "first_32": [round(float(x), 6) for x in flat[:32]],
    }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

class _StepContext:
    def __init__(self, trace: Trace, step_name: str, inputs: Any) -> None:
        self._trace = trace
        self._step_name = step_name
        self._record: dict[str, Any] = {"inputs": inputs, "outputs": None}

    def __enter__(self) -> dict[str, Any]:
        return self._record

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._trace.record(
            self._step_name,
            inputs=self._record.get("inputs"),
            outputs=self._record.get("outputs"),
            metadata=self._record.get("metadata"),
        )
        # Never suppress exceptions
        return False


def _serialise(obj: Any) -> Any:
    """Best-effort conversion to JSON-serialisable types."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _serialise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialise(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, np.ndarray):
        return {"_type": "ndarray", "shape": list(obj.shape), "stats": _embedding_stats(obj)}
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    # Dataclasses / objects with __dict__
    if hasattr(obj, "__dict__"):
        return _serialise(vars(obj))
    # Fallback
    try:
        return str(obj)
    except Exception:
        return "<unserializable>"
