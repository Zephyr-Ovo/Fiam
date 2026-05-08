"""Server-side Stroll source storage.

Stroll records are point-only. The stable archive unit is a rough 50m cell,
but nearby reads still use true distance filtering so edge cases near cell
borders work as expected.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

from fiam.config import FiamConfig

PLACE_KINDS = {"road", "green", "building", "water", "unknown"}
ORIGINS = {"user", "ai", "phone", "limen", "replay"}
RECORD_KINDS = {"note", "photo", "marker", "action"}
ACTION_TYPES = {"view_camera", "capture_photo", "set_limen_screen", "refresh_nearby"}

EARTH_RADIUS_M = 6_371_000
LAT_METERS = 111_320
DEFAULT_RADIUS_M = 50

_STROLL_RECORD_RE = re.compile(
    r"<\s*(?:stroll_record|stroll_marker)\b(?P<attrs>[^<>]*?)\s*"
    r"(?:/>|>(?P<body>.*?)</\s*(?:stroll_record|stroll_marker)\s*>)",
    re.DOTALL | re.IGNORECASE,
)
_STROLL_ACTION_RE = re.compile(
    r"<\s*stroll_action\b(?P<attrs>[^<>]*?)\s*"
    r"(?:/>|>(?P<body>.*?)</\s*stroll_action\s*>)",
    re.DOTALL | re.IGNORECASE,
)
_ATTR_RE = re.compile(r"([A-Za-z_][\w:-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def stroll_dir(config: FiamConfig) -> Path:
    path = config.home_path / "stroll"
    path.mkdir(parents=True, exist_ok=True)
    return path


def messages_path(config: FiamConfig) -> Path:
    return stroll_dir(config) / "messages.jsonl"


def spatial_records_path(config: FiamConfig) -> Path:
    return stroll_dir(config) / "spatial_records.jsonl"


def actions_path(config: FiamConfig) -> Path:
    return stroll_dir(config) / "actions.jsonl"


def cell_id(lng: float, lat: float, cell_size_m: int = DEFAULT_RADIUS_M) -> str:
    lng_meters = LAT_METERS * max(0.12, math.cos(math.radians(lat)))
    y = math.floor((lat * LAT_METERS) / cell_size_m)
    x = math.floor((lng * lng_meters) / cell_size_m)
    return f"{cell_size_m}m:{y}:{x}"


def neighbor_cell_ids(cell: str) -> set[str]:
    try:
        size_raw, y_raw, x_raw = cell.split(":", 2)
        y = int(y_raw)
        x = int(x_raw)
    except ValueError:
        return {cell}
    return {f"{size_raw}:{y + dy}:{x + dx}" for dy in range(-1, 2) for dx in range(-1, 2)}


def distance_meters(first: dict[str, Any], second: dict[str, Any]) -> float:
    first_lat = math.radians(float(first["lat"]))
    second_lat = math.radians(float(second["lat"]))
    lat_delta = math.radians(float(second["lat"]) - float(first["lat"]))
    lng_delta = math.radians(float(second["lng"]) - float(first["lng"]))
    seed = math.sin(lat_delta / 2) ** 2 + math.cos(first_lat) * math.cos(second_lat) * math.sin(lng_delta / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(seed), math.sqrt(1 - seed))


def bearing_degrees(first: dict[str, Any], second: dict[str, Any]) -> float:
    first_lat = math.radians(float(first["lat"]))
    second_lat = math.radians(float(second["lat"]))
    lng_delta = math.radians(float(second["lng"]) - float(first["lng"]))
    y = math.sin(lng_delta) * math.cos(second_lat)
    x = math.cos(first_lat) * math.sin(second_lat) - math.sin(first_lat) * math.cos(second_lat) * math.cos(lng_delta)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def normalize_place_kind(value: Any) -> str:
    return value if value in PLACE_KINDS else "unknown"


def strip_spatial_record_markers(text: str) -> str:
    return _STROLL_RECORD_RE.sub("", text or "").strip()


def strip_stroll_action_markers(text: str) -> str:
    return _STROLL_ACTION_RE.sub("", text or "").strip()


def apply_spatial_record_markers(config: FiamConfig, text: str, context: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    current = context.get("current") if isinstance(context, dict) and isinstance(context.get("current"), dict) else None
    records: list[dict[str, Any]] = []
    for match in _STROLL_RECORD_RE.finditer(text or ""):
        attrs = _marker_attrs(match.group("attrs") or "")
        body = (match.group("body") or "").strip()
        try:
            lng = float(attrs.get("lng") or (current or {}).get("lng"))
            lat = float(attrs.get("lat") or (current or {}).get("lat"))
        except (TypeError, ValueError):
            continue
        payload: dict[str, Any] = {
            "kind": attrs.get("kind") or "marker",
            "origin": attrs.get("origin") or "ai",
            "lng": lng,
            "lat": lat,
            "placeKind": attrs.get("placeKind") or (context or {}).get("placeKind") or (current or {}).get("placeKind") or "unknown",
            "radiusM": attrs.get("radiusM") or attrs.get("radius") or DEFAULT_RADIUS_M,
            "text": attrs.get("text") or attrs.get("title") or body,
            "emoji": attrs.get("emoji") or "",
        }
        records.append(add_spatial_record(config, payload))
    return strip_spatial_record_markers(text), records


def apply_stroll_action_markers(config: FiamConfig, text: str, context: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    current = context.get("current") if isinstance(context, dict) and isinstance(context.get("current"), dict) else None
    actions: list[dict[str, Any]] = []
    for match in _STROLL_ACTION_RE.finditer(text or ""):
        attrs = _marker_attrs(match.group("attrs") or "")
        body = (match.group("body") or "").strip()
        action_type = str(attrs.get("type") or attrs.get("action") or "").strip().lower()
        if action_type not in ACTION_TYPES:
            continue
        payload = {
            "type": action_type,
            "reason": attrs.get("reason") or body,
            "text": attrs.get("text") or attrs.get("message") or "",
            "emoji": attrs.get("emoji") or "",
            "current": current or {},
            "placeKind": attrs.get("placeKind") or (context or {}).get("placeKind") or (current or {}).get("placeKind") or "unknown",
        }
        actions.append(queue_client_action(config, payload))
    return strip_stroll_action_markers(text), actions


def _marker_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw or ""):
        attrs[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3) or ""
    return attrs


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")


def add_spatial_record(config: FiamConfig, payload: dict[str, Any]) -> dict[str, Any]:
    lng = float(payload["lng"])
    lat = float(payload["lat"])
    now_ms = int(time.time() * 1000)
    kind = str(payload.get("kind") or "note").strip().lower()
    origin = str(payload.get("origin") or "user").strip().lower()
    if kind not in RECORD_KINDS:
        raise ValueError("invalid stroll record kind")
    if origin not in ORIGINS:
        raise ValueError("invalid stroll record origin")
    record = {
        "id": str(payload.get("id") or f"stroll-{now_ms}-{hashlib.sha1(f'{lng}:{lat}:{now_ms}'.encode()).hexdigest()[:8]}"),
        "kind": kind,
        "lng": lng,
        "lat": lat,
        "cellId": str(payload.get("cellId") or cell_id(lng, lat)),
        "radiusM": float(payload.get("radiusM") or DEFAULT_RADIUS_M),
        "placeKind": normalize_place_kind(payload.get("placeKind")),
        "origin": origin,
        "createdAt": int(payload.get("createdAt") or now_ms),
        "updatedAt": int(payload.get("updatedAt") or now_ms),
    }
    for key in ("text", "emoji"):
        value = str(payload.get(key) or "").strip()
        if value:
            record[key] = value
    attachment = payload.get("attachment")
    if isinstance(attachment, dict):
        record["attachment"] = attachment
    _append_jsonl(spatial_records_path(config), record)
    return record


def list_spatial_records(config: FiamConfig, *, current: dict[str, Any] | None = None, radius_m: float = DEFAULT_RADIUS_M, changed_since: int = 0) -> dict[str, Any]:
    rows = _load_jsonl(spatial_records_path(config))
    if changed_since:
        rows = [row for row in rows if int(row.get("updatedAt") or row.get("createdAt") or 0) > changed_since]
    if current and "lng" in current and "lat" in current:
        current_cell = cell_id(float(current["lng"]), float(current["lat"]))
        cells = neighbor_cell_ids(current_cell)
        filtered = []
        for row in rows:
            if row.get("cellId") not in cells:
                continue
            distance = distance_meters(current, row)
            if distance <= radius_m:
                item = dict(row)
                item["distanceM"] = distance
                item["bearingDeg"] = bearing_degrees(current, row)
                filtered.append(item)
        rows = sorted(filtered, key=lambda item: float(item.get("distanceM") or 0))
    version = context_version(rows)
    return {"ok": True, "records": rows, "contextVersion": version}


def context_version(records: list[dict[str, Any]]) -> str:
    seed = "|".join(f"{row.get('id')}:{row.get('updatedAt') or row.get('createdAt')}" for row in records)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def build_context_block(config: FiamConfig, payload_context: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    context = dict(payload_context or {})
    current = context.get("current") if isinstance(context.get("current"), dict) else None
    radius_m = float(context.get("radiusM") or DEFAULT_RADIUS_M)
    nearby = list_spatial_records(config, current=current, radius_m=radius_m) if current else {"ok": True, "records": [], "contextVersion": context_version([])}
    if current and "lng" in current and "lat" in current:
        context["cellId"] = str(context.get("cellId") or cell_id(float(current["lng"]), float(current["lat"])))
    context["placeKind"] = normalize_place_kind(context.get("placeKind"))
    context["spatialRecords"] = nearby.get("records", [])
    context["contextVersion"] = nearby.get("contextVersion", "")
    lines = ["[stroll_context]", f"cell={context.get('cellId', '')} place={context.get('placeKind', 'unknown')} version={context.get('contextVersion', '')}"]
    lines.append('stroll_xml: use short hidden tags only when needed: <stroll_record kind="marker" text="short label" emoji="*" />; <stroll_action type="view_camera" reason="why" />; <stroll_action type="capture_photo" reason="why" />; <stroll_action type="set_limen_screen" text="spark" />; <stroll_action type="refresh_nearby" reason="why" />. Omit lng/lat to use current point. Limen screen renders ASCII only: prefer text="<short lowercase word>" or text="(^_^)" style kaomoji, or text="emoji:heart" / text="emoji:smile" (only heart and smile are drawn). Do NOT put unicode emoji like ✨💖 into text — they render as a generic geometric fallback. Save unicode emoji for stroll_record / map markers, not Limen.')
    if current:
        lines.append(f"current lat={current.get('lat')} lng={current.get('lng')} accuracy={current.get('accuracy', '')}")
    records = context.get("spatialRecords") or []
    if records:
        lines.append("nearby_records<=50m:")
        for row in records[:12]:
            text = str(row.get("text") or row.get("kind") or "record").replace("\n", " ")[:140]
            lines.append(f"- {row.get('id')} {row.get('kind')} {row.get('origin')} {float(row.get('distanceM') or 0):.1f}m {text}")
    else:
        lines.append("nearby_records<=50m: none changed/available")
    return "\n".join(lines), context


def record_action_result(config: FiamConfig, payload: dict[str, Any]) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    record = {
        "id": str(payload.get("id") or payload.get("actionId") or f"action-{now_ms}"),
        "action": str(payload.get("action") or payload.get("type") or "unknown"),
        "status": str(payload.get("status") or "reported"),
        "createdAt": now_ms,
        "payload": payload,
    }
    _append_jsonl(actions_path(config), record)
    return record


def queue_client_action(config: FiamConfig, payload: dict[str, Any]) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    action_type = str(payload.get("type") or payload.get("action") or "unknown")
    record = {
        "id": str(payload.get("id") or f"stroll-action-{now_ms}-{hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]}"),
        "type": action_type,
        "status": "queued",
        "origin": "ai",
        "createdAt": now_ms,
        "payload": payload,
    }
    _append_jsonl(actions_path(config), record)
    return record