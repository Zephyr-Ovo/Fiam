"""Annotation API helpers for the debug dashboard."""

from __future__ import annotations

import json
import logging
import re
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_ROOT: Path | None = None
_CONFIG: Any = None
_POOL: Any = None
_COMPUTE_LOCK: Any = None
_GET_EMBEDDER: Callable[[], Any] | None = None
_LOGGER = logging.getLogger(__name__)
_ANNOTATION_PROPOSAL: dict | None = None


def configure(
	*,
	root: Path,
	config: Any,
	pool: Any,
	compute_lock: Any,
	get_embedder: Callable[[], Any],
	logger: logging.Logger | None = None,
) -> None:
	global _ROOT, _CONFIG, _POOL, _COMPUTE_LOCK, _GET_EMBEDDER, _LOGGER
	_ROOT = root
	_CONFIG = config
	_POOL = pool
	_COMPUTE_LOCK = compute_lock
	_GET_EMBEDDER = get_embedder
	if logger is not None:
		_LOGGER = logger


def _lock():
	return _COMPUTE_LOCK if _COMPUTE_LOCK is not None else nullcontext()


def annotation_state() -> dict:
	if not _CONFIG:
		return {"processed_until": 0}
	path = _CONFIG.annotation_state_path
	if not path.exists():
		return {"processed_until": 0}
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, OSError):
		return {"processed_until": 0}
	return {"processed_until": int(data.get("processed_until", 0))}


def save_annotation_state(processed_until: int) -> None:
	if not _CONFIG:
		return
	path = _CONFIG.annotation_state_path
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(
		json.dumps({"processed_until": int(processed_until)}, indent=2),
		encoding="utf-8",
	)


def safe_event_id(raw: str, fallback: str, reserved: set[str] | None = None) -> str:
	reserved = reserved or set()
	name = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff]+", "_", str(raw or "").strip())
	name = re.sub(r"_+", "_", name).strip("_")
	if not name:
		name = fallback
	if len(name) > 60:
		name = name[:60].rstrip("_")
	if name not in reserved and (_POOL is None or _POOL.get_event(name) is None):
		return name
	base = name
	i = 2
	while (_POOL and _POOL.get_event(f"{base}_{i}") is not None) or f"{base}_{i}" in reserved:
		i += 1
	return f"{base}_{i}"


def beat_vectors_from_store(beats: list[dict]) -> list:
	if not _CONFIG:
		return []
	try:
		from fiam.store.beat import Beat
		from fiam.store.features import FeatureStore
		store = FeatureStore(_CONFIG.feature_dir, dim=_CONFIG.embedding_dim)
		vectors = []
		for raw in beats:
			try:
				vectors.append(store.get_beat_vector(Beat.from_dict(raw)))
			except Exception:
				vectors.append(None)
		return vectors
	except Exception:
		return []


def parse_beat_time(raw: str):
	try:
		dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		return dt
	except (TypeError, ValueError):
		return datetime.now(timezone.utc)


def normalize_app_beat_dict(item: dict) -> dict:
	channel = str(item.get("channel") or "").strip().lower()
	surface = str(item.get("surface") or "").strip().lower()
	if channel in {"favilla", "app"}:
		item = dict(item)
		item["channel"] = "chat"
		item["surface"] = "favilla" if not surface or surface in {"favilla", "app"} or surface.startswith("favilla.") else surface
	elif surface == "app" or surface.startswith("favilla."):
		item = dict(item)
		item["surface"] = "favilla"
	elif surface.startswith("atrium."):
		item = dict(item)
		item["surface"] = "atrium"
	return item


def annotate_request(payload: dict) -> dict:
	"""Load unprocessed event-store beats for manual annotation."""
	global _ANNOTATION_PROPOSAL
	if not _CONFIG:
		raise RuntimeError("config not loaded")

	from fiam.store.beat import read_beats

	all_beats = []
	for beat in read_beats(_CONFIG.flow_path):
		item = normalize_app_beat_dict(beat.to_dict())
		item["text"] = item.get("content", "")
		all_beats.append(item)
	total = len(all_beats)
	state = annotation_state()
	limit = max(1, int(payload.get("limit", 100)))
	requested_offset = int(payload.get("offset", state["processed_until"]))
	offset = max(requested_offset, state["processed_until"])
	end = min(offset + limit, total)

	beats = all_beats[offset:end]

	if not beats:
		raise ValueError("no beats to annotate")

	cuts = [0] * max(0, len(beats) - 1)
	drift_cuts = [0] * max(0, len(beats) - 1)

	_ANNOTATION_PROPOSAL = {
		"beats": beats,
		"cuts": cuts,
		"drift_cuts": drift_cuts,
		"edges": [],
		"names": {},
		"flow_offset": offset,
		"flow_end": end,
		"processed_until": state["processed_until"],
		"status": "cuts_proposed",
	}
	return _ANNOTATION_PROPOSAL


def annotate_edges(payload: dict) -> dict:
	"""Phase 2: after human reviews cuts, request edge proposals."""
	global _ANNOTATION_PROPOSAL
	if not _CONFIG or not _POOL:
		raise RuntimeError("config/pool not loaded")
	if not _ANNOTATION_PROPOSAL:
		raise ValueError("no active proposal — run /annotate/request first")

	cuts = payload.get("cuts", _ANNOTATION_PROPOSAL.get("cuts", []))
	drift_cuts = payload.get("drift_cuts", _ANNOTATION_PROPOSAL.get("drift_cuts", []))
	beats = _ANNOTATION_PROPOSAL["beats"]

	from fiam.annotator import cuts_to_segments, propose_edges
	segments = cuts_to_segments(beats, cuts)

	new_events: list[dict] = []
	for i, seg in enumerate(segments):
		start, end = seg["start"], seg["end"]
		body_lines = [b.get("text", "") for b in beats[start:end + 1]]
		new_events.append({
			"id": f"seg_{i}",
			"time": beats[start].get("t", ""),
			"body": "\n".join(body_lines),
		})

	existing_events: list[dict] = []
	pool_events = _POOL.load_events()
	for ev in pool_events:
		body = _POOL.read_body(ev.id)
		existing_events.append({
			"id": ev.id,
			"time": ev.t.isoformat(),
			"body": body[:400],
		})

	result = propose_edges(new_events, existing_events, _CONFIG)

	_ANNOTATION_PROPOSAL["cuts"] = cuts
	_ANNOTATION_PROPOSAL["drift_cuts"] = drift_cuts
	_ANNOTATION_PROPOSAL["edges"] = result["edges"]
	_ANNOTATION_PROPOSAL["names"] = result.get("names", {})
	_ANNOTATION_PROPOSAL["status"] = "edges_proposed"
	return _ANNOTATION_PROPOSAL


def annotate_confirm(payload: dict) -> dict:
	"""Confirm annotations: save training data with vectors and create events."""
	global _ANNOTATION_PROPOSAL
	if not _CONFIG or not _POOL:
		raise RuntimeError("config/pool not loaded")
	if not _ANNOTATION_PROPOSAL:
		raise ValueError("no active proposal")
	if _ROOT is None:
		raise RuntimeError("annotation module not configured")

	beats = _ANNOTATION_PROPOSAL["beats"]
	cuts = payload.get("cuts", _ANNOTATION_PROPOSAL.get("cuts", []))
	drift_cuts = payload.get("drift_cuts", _ANNOTATION_PROPOSAL.get("drift_cuts", []))
	edges = payload.get("edges", _ANNOTATION_PROPOSAL.get("edges", []))
	names = _ANNOTATION_PROPOSAL.get("names", {})

	import numpy as np
	from fiam.store.pool import Pool, Event
	from fiam.annotator import save_training_data, cuts_to_segments

	beat_vectors: list | None = beat_vectors_from_store(beats)
	with _lock():
		if not beat_vectors or not any(v is not None for v in beat_vectors):
			embedder = _GET_EMBEDDER() if _GET_EMBEDDER else None
			vecs = []
			if embedder:
				for beat in beats:
					text = beat.get("text", "").strip()
					if text:
						try:
							vecs.append(embedder.embed(text))
						except Exception:
							vecs.append(None)
					else:
						vecs.append(None)
			beat_vectors = vecs

	segments = cuts_to_segments(beats, cuts)
	seg_to_event_id: dict[str, str] = {}
	reserved_ids: set[str] = set()
	for i, _seg in enumerate(segments):
		seg_id = f"seg_{i}"
		fallback = f"ann_{_ANNOTATION_PROPOSAL['flow_offset']}_{i}"
		event_id = safe_event_id(names.get(seg_id, ""), fallback, reserved_ids)
		reserved_ids.add(event_id)
		seg_to_event_id[seg_id] = event_id

	normalized_edges = []
	for edge in edges:
		normalized = dict(edge)
		normalized["src"] = seg_to_event_id.get(str(edge.get("src", "")), edge.get("src"))
		normalized["dst"] = seg_to_event_id.get(str(edge.get("dst", "")), edge.get("dst"))
		normalized_edges.append(normalized)

	training_dir = _ROOT / "training_data"
	stats = save_training_data(
		beats, cuts, normalized_edges, training_dir,
		beat_vectors=beat_vectors,
		drift_cuts=drift_cuts,
	)

	created_events: list[str] = []
	created_event_times: dict[str, tuple] = {}

	with _lock():
		for i, seg in enumerate(segments):
			start, end = seg["start"], seg["end"]
			body_lines = [b.get("text", "") for b in beats[start:end + 1]]
			body = "\n".join(body_lines)
			event_id = seg_to_event_id[f"seg_{i}"]

			t_start = parse_beat_time(beats[start].get("t", ""))
			t_end = parse_beat_time(beats[end].get("t", ""))

			seg_vecs = []
			if beat_vectors:
				for idx in range(start, end + 1):
					if beat_vectors[idx] is not None:
						seg_vecs.append(beat_vectors[idx])
			fingerprint = np.mean(seg_vecs, axis=0).astype(np.float32) if seg_vecs else None
			if fingerprint is not None:
				norm = np.linalg.norm(fingerprint)
				if norm > 1e-9:
					fingerprint = (fingerprint / norm).astype(np.float32)

			_POOL.write_body(event_id, body)
			fp_idx = -1
			if fingerprint is not None:
				fp_idx = _POOL.append_fingerprint(fingerprint)

			ev = Event(id=event_id, t=t_start, access_count=0, fingerprint_idx=fp_idx)
			_POOL.append_event(ev)
			created_events.append(event_id)
			created_event_times[event_id] = (t_start, t_end)

		_POOL.rebuild_cosine()

		edge_map: dict[tuple[str, str], tuple[str, float]] = {}
		for a, b in zip(created_events, created_events[1:]):
			_a_start, a_end = created_event_times[a]
			b_start, _b_end = created_event_times[b]
			gap = max(0.0, (b_start - a_end).total_seconds())
			if gap <= 1800:
				weight = max(0.05, 0.2 * (1.0 - gap / 1800.0))
				edge_map[(a, b)] = ("temporal", weight)

		for edge in normalized_edges:
			src = str(edge.get("src", ""))
			dst = str(edge.get("dst", ""))
			if not src or not dst or src == dst:
				continue
			edge_map[(src, dst)] = (str(edge.get("type", "semantic")), float(edge.get("weight", 0.5)))

		created_edges = 0
		for (src, dst), (kind, weight) in edge_map.items():
			try:
				src_ev = _POOL.get_event(src)
				dst_ev = _POOL.get_event(dst)
				if src_ev and dst_ev and src_ev.fingerprint_idx >= 0 and dst_ev.fingerprint_idx >= 0:
					_POOL.add_edge(
						src_ev.fingerprint_idx,
						dst_ev.fingerprint_idx,
						Pool.edge_type_id(kind),
						weight,
					)
					created_edges += 1
			except Exception as exc:
				_LOGGER.warning("edge creation failed: %s", exc)

	save_annotation_state(int(_ANNOTATION_PROPOSAL.get("flow_end", 0)))
	_ANNOTATION_PROPOSAL = None

	return {
		"ok": True,
		"events_created": created_events,
		"edges_created": created_edges,
		**stats,
	}


def annotate_proposal() -> dict:
	"""Return current pending proposal."""
	if not _ANNOTATION_PROPOSAL:
		return {"status": "none"}
	return _ANNOTATION_PROPOSAL
