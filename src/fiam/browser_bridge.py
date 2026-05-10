"""Browser extension bridge helpers.

The extension sends a compact page snapshot; this module keeps the wire payload
small, stable, and independent from any specific browser implementation.
"""

from __future__ import annotations

import json
import re
from html import unescape
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_TEXT = 220
MAX_NODE_TEXT = 120
MAX_SELECTION = 1200
MAX_HEADINGS = 12
MAX_TEXT_BLOCKS = 12
MAX_TEXT_BLOCK_CHARS = 900
MAX_NODES = 32
MAX_MEDIA_SAMPLES = 8
PROFILE_DIR = Path(__file__).resolve().parents[2] / "channels" / "atrium" / "browser-profiles"
BROWSER_ACTION_RE = re.compile(r"<browser_action\b([^>]*)\s*/>|<browser_action\b([^>]*)>.*?</browser_action>", re.IGNORECASE | re.DOTALL)
BROWSER_DONE_RE = re.compile(r"<browser_done\b([^>]*)\s*/>|<browser_done\b([^>]*)>.*?</browser_done>", re.IGNORECASE | re.DOTALL)
ACTION_ATTR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(['\"])(.*?)\2")

ACTION_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "menuitem",
    "radio",
    "searchbox",
    "switch",
    "tab",
    "textbox",
}

ROLE_PRIORITY = {
    "searchbox": 0,
    "textbox": 1,
    "combobox": 2,
    "button": 3,
    "tab": 4,
    "checkbox": 5,
    "radio": 5,
    "switch": 5,
    "menuitem": 6,
    "link": 8,
}


def _clean_text(value: Any, *, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


@lru_cache(maxsize=1)
def _load_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if not PROFILE_DIR.exists():
        return profiles
    for path in sorted(PROFILE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            profiles.append(data)
    return profiles


def _host_matches(host: str, pattern: str) -> bool:
    host = host.lower().strip(".")
    pattern = pattern.lower().strip(".")
    return host == pattern or host.endswith(f".{pattern}")


def _profile_for_url(url: str) -> dict[str, Any] | None:
    host = urlparse(url).hostname or ""
    if not host:
        return None
    for profile in _load_profiles():
        hosts = profile.get("hosts") or []
        if any(_host_matches(host, str(item)) for item in hosts):
            return profile
    return None


def _profile_matches_url(profile: dict[str, Any], url: str) -> bool:
    host = urlparse(url).hostname or ""
    hosts = profile.get("hosts") or []
    return not hosts or any(_host_matches(host, str(item)) for item in hosts)


def _merge_profiles(base: dict[str, Any] | None, override: dict[str, Any] | None) -> dict[str, Any] | None:
    if not base and not override:
        return None
    if not override:
        return dict(base or {})
    if not base:
        return dict(override)
    merged = dict(base)
    merged["id"] = "+".join(item for item in [str(base.get("id") or ""), str(override.get("id") or "")] if item)
    merged["hosts"] = list(dict.fromkeys([*(base.get("hosts") or []), *(override.get("hosts") or [])]))
    if override.get("strictKeep") or override.get("strict_keep"):
        merged["strictKeep"] = True
        merged["keep"] = _list_of_dicts(override.get("keep"))
    else:
        merged["keep"] = [*_list_of_dicts(override.get("keep")), *_list_of_dicts(base.get("keep"))]
    merged["suppress"] = [*_list_of_dicts(override.get("suppress")), *_list_of_dicts(base.get("suppress"))]
    groups = {}
    if isinstance(base.get("groups"), dict):
        groups.update(base["groups"])
    if isinstance(override.get("groups"), dict):
        groups.update(override["groups"])
    if groups:
        merged["groups"] = groups
    if override.get("maxNodes") or override.get("max_nodes"):
        merged["maxNodes"] = override.get("maxNodes") or override.get("max_nodes")
    if base.get("strictKeepContextFallback") or base.get("strict_keep_context_fallback") or override.get("strictKeepContextFallback") or override.get("strict_keep_context_fallback"):
        merged["strictKeepContextFallback"] = True
    return merged


def _profile_for_page(url: str, page: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    base = _profile_for_url(url)
    override = page.get("profileRules") or page.get("profile_rules") or payload.get("profileRules") or payload.get("profile_rules")
    if not isinstance(override, dict) or not _profile_matches_url(override, url):
        override = None
    return _merge_profiles(base, override)


def _node_label(node: dict[str, Any]) -> str:
    return str(node.get("name") or node.get("text") or node.get("href") or node.get("id") or "")


def _rule_matches(node: dict[str, Any], rule: dict[str, Any]) -> bool:
    role = str(rule.get("role") or "").strip().lower()
    if role and role != str(node.get("role") or "").strip().lower():
        return False
    label = _node_label(node).casefold()
    contains = str(rule.get("labelContains") or rule.get("label_contains") or "").casefold().strip()
    if contains and contains not in label:
        return False
    regex = str(rule.get("labelRegex") or rule.get("label_regex") or "").strip()
    if regex:
        try:
            if not re.search(regex, _node_label(node), flags=re.IGNORECASE):
                return False
        except re.error:
            return False
    href_contains = str(rule.get("hrefContains") or rule.get("href_contains") or "").casefold().strip()
    if href_contains and href_contains not in str(node.get("href") or "").casefold():
        return False
    selector = str(node.get("selector") or "")
    selector_contains = str(rule.get("selectorContains") or rule.get("selector_contains") or "").casefold().strip()
    if selector_contains and selector_contains not in selector.casefold():
        return False
    selector_regex = str(rule.get("selectorRegex") or rule.get("selector_regex") or "").strip()
    if selector_regex:
        try:
            if not re.search(selector_regex, selector, flags=re.IGNORECASE):
                return False
        except re.error:
            return False
    return True


def _profile_keep_rule(node: dict[str, Any], profile: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    for index, rule in enumerate(_list_of_dicts(profile.get("keep"))):
        if _rule_matches(node, rule):
            return index, rule
    return None


def _profile_suppress_group(node: dict[str, Any], profile: dict[str, Any]) -> str:
    for rule in _list_of_dicts(profile.get("suppress")):
        if _rule_matches(node, rule):
            return str(rule.get("group") or "suppressed")
    return ""


def _rect(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value[:4]:
        try:
            out.append(int(float(item)))
        except (TypeError, ValueError):
            out.append(0)
    return out if len(out) == 4 else []


def _viewport_rank(node: dict[str, Any]) -> int:
    viewport = _clean_text(node.get("viewport"), limit=24).lower()
    return 0 if viewport == "visible" else 1


def _node_priority(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
    index, node = item
    role = _clean_text(node.get("role"), limit=48).lower()
    return (_viewport_rank(node), ROLE_PRIORITY.get(role, 9), index)


def _take_with_budget(values: list[str], *, max_items: int, max_chars: int) -> list[str]:
    out: list[str] = []
    used = 0
    for value in values:
        clean = _clean_text(value)
        if not clean or clean in out:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(clean) > remaining:
            clean = _clean_text(clean, limit=max(24, remaining))
        out.append(clean)
        used += len(clean)
        if len(out) >= max_items:
            break
    return out


def _normalize_media(page: dict[str, Any]) -> dict[str, Any]:
    media = page.get("media") if isinstance(page.get("media"), dict) else {}
    samples: list[dict[str, Any]] = []
    for item in _list_of_dicts(media.get("samples"))[:MAX_MEDIA_SAMPLES]:
        kind = _clean_text(item.get("kind"), limit=24).lower() or "media"
        label = _clean_text(item.get("label"), limit=120)
        samples.append({
            "kind": kind,
            "label": label,
            "rect": _rect(item.get("rect")),
            "viewport": _clean_text(item.get("viewport"), limit=24).lower(),
        })
    return {
        "imageCount": int(media.get("imageCount") or media.get("image_count") or 0),
        "imageElementCount": int(media.get("imageElementCount") or media.get("image_element_count") or 0),
        "backgroundImageCount": int(media.get("backgroundImageCount") or media.get("background_image_count") or 0),
        "canvasCount": int(media.get("canvasCount") or media.get("canvas_count") or 0),
        "videoCount": int(media.get("videoCount") or media.get("video_count") or 0),
        "iframeCount": int(media.get("iframeCount") or media.get("iframe_count") or 0),
        "samples": samples,
    }


def normalize_browser_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
    page = dict(snapshot)
    nodes = _list_of_dicts(page.get("nodes"))
    url = _clean_text(page.get("url"), limit=512)
    profile = _profile_for_page(url, page, payload)
    profile_max_nodes = int(profile.get("maxNodes") or profile.get("max_nodes") or MAX_NODES) if profile else MAX_NODES
    headings = [_clean_text(item, limit=160) for item in page.get("headings") or []]
    text_blocks = [_clean_text(item, limit=180) for item in page.get("textBlocks") or page.get("text_blocks") or []]

    profile_counts: dict[str, int] = {}
    strict_keep = bool((profile or {}).get("strictKeep") or (profile or {}).get("strict_keep"))
    sorted_nodes = sorted(enumerate(nodes), key=_node_priority)
    if profile:
        def profile_sort(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
            keep = _profile_keep_rule(item[1], profile)
            keep_rank = keep[0] if keep else 999
            base = _node_priority(item)
            return (0 if keep else 1, keep_rank, base[0], item[0])
        sorted_nodes = sorted(sorted_nodes, key=profile_sort)

    def collect_nodes(*, ignore_strict_keep: bool = False, max_nodes: int | None = None) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
        compact: list[dict[str, Any]] = []
        actions_by_id: dict[str, dict[str, Any]] = {}
        aliases: dict[str, str] = {}
        seen: set[tuple[str, str]] = set()
        limit = max(1, min(MAX_NODES, max_nodes or profile_max_nodes))
        for index, node in sorted_nodes:
            role = _clean_text(node.get("role"), limit=48).lower()
            name = _clean_text(node.get("name") or node.get("label") or node.get("text"), limit=MAX_NODE_TEXT)
            text = _clean_text(node.get("text"), limit=MAX_NODE_TEXT)
            selector = _clean_text(node.get("selector"), limit=360)
            href = _clean_text(node.get("href"), limit=360)
            if not role and not name and not text:
                continue
            if role not in ACTION_ROLES:
                continue
            label_key = (name or text or selector).casefold()
            if not label_key:
                continue
            dedupe_key = (role, label_key)
            if dedupe_key in seen:
                continue
            keep_rule = _profile_keep_rule(node, profile) if profile else None
            suppress_group = _profile_suppress_group(node, profile) if profile else ""
            if strict_keep and not ignore_strict_keep and not keep_rule:
                profile_counts["strict_keep_hidden"] = profile_counts.get("strict_keep_hidden", 0) + 1
                continue
            if suppress_group and not keep_rule:
                profile_counts[suppress_group] = profile_counts.get(suppress_group, 0) + 1
                continue
            seen.add(dedupe_key)
            node_id = _clean_text(node.get("id") or f"node_{index + 1}", limit=64)
            actions = [str(action) for action in (node.get("actions") or [])[:6]]
            alias = _clean_text((keep_rule[1].get("alias") if keep_rule else "") or "", limit=MAX_NODE_TEXT)
            display_name = alias or name
            if alias:
                aliases[node_id] = alias
            compact.append({
                "id": node_id,
                "role": role or "unknown",
                "name": display_name,
                "text": text,
                "href": href,
                "viewport": _clean_text(node.get("viewport"), limit=24).lower(),
                "rect": _rect(node.get("rect")),
                "actions": actions,
            })
            actions_by_id[node_id] = {
                "role": role or "unknown",
                "name": name,
                "selector": selector,
                "href": href,
                "rect": _rect(node.get("rect")),
                "actions": actions,
            }
            if len(compact) >= limit:
                break
        return compact, actions_by_id, aliases

    compact_nodes, action_map, profile_aliases = collect_nodes()
    strict_context_fallback = bool((profile or {}).get("strictKeepContextFallback") or (profile or {}).get("strict_keep_context_fallback"))
    if strict_keep and strict_context_fallback and compact_nodes and nodes:
        context_limit = min(MAX_NODES, max(profile_max_nodes, len(compact_nodes) + 6))
        context_nodes, context_map, context_aliases = collect_nodes(ignore_strict_keep=True, max_nodes=context_limit)
        existing_labels = {(node.get("role"), (node.get("name") or node.get("text") or "").casefold()) for node in compact_nodes}
        for node in context_nodes:
            label_key = (node.get("role"), (node.get("name") or node.get("text") or "").casefold())
            if label_key in existing_labels:
                continue
            compact_nodes.append(node)
            action_map[node["id"]] = context_map[node["id"]]
            if node["id"] in context_aliases:
                profile_aliases[node["id"]] = context_aliases[node["id"]]
            existing_labels.add(label_key)
            if len(compact_nodes) >= context_limit:
                break
    if strict_keep and strict_context_fallback and not compact_nodes and nodes:
        compact_nodes, action_map, profile_aliases = collect_nodes(ignore_strict_keep=True, max_nodes=min(profile_max_nodes, 8))
    profile_summary = None
    if profile:
        group_labels = profile.get("groups") if isinstance(profile.get("groups"), dict) else {}
        group_labels = dict(group_labels)
        if strict_keep:
            group_labels.setdefault("strict_keep_hidden", "non-selected controls")
        profile_summary = {
            "id": _clean_text(profile.get("id"), limit=80),
            "strictKeep": strict_keep,
            "strictKeepContextFallback": strict_context_fallback,
            "aliases": profile_aliases,
            "suppressed": [
                {"group": group, "label": _clean_text(group_labels.get(group) or group, limit=120), "count": count}
                for group, count in sorted(profile_counts.items())
                if count > 0
            ],
        }

    return {
        "schema": "fiam.browser.snapshot.v1",
        "url": url,
        "title": _clean_text(page.get("title"), limit=240),
        "browser": _clean_text(page.get("browser") or payload.get("browser"), limit=80),
        "tabId": _clean_text(page.get("tabId") or page.get("tab_id") or payload.get("tabId"), limit=80),
        "capturedAt": _clean_text(page.get("capturedAt") or page.get("captured_at"), limit=80),
        "selection": _clean_text(page.get("selection"), limit=MAX_SELECTION),
        "headings": [item for item in headings if item][:MAX_HEADINGS],
        "textBlocks": _take_with_budget([item for item in text_blocks if item], max_items=MAX_TEXT_BLOCKS, max_chars=MAX_TEXT_BLOCK_CHARS),
        "nodes": compact_nodes,
        "actionMap": action_map,
        "media": _normalize_media(page),
        "profile": profile_summary,
        "rawNodeCount": len(nodes),
    }


def browser_snapshot_meta(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = normalize_browser_snapshot(payload)
    return {
        "schema": snapshot["schema"],
        "url": snapshot["url"],
        "title": snapshot["title"],
        "browser": snapshot["browser"],
        "tabId": snapshot["tabId"],
        "nodeCount": len(snapshot["nodes"]),
        "rawNodeCount": snapshot["rawNodeCount"],
        "profile": snapshot.get("profile"),
        "media": {
            "imageCount": snapshot["media"]["imageCount"],
            "backgroundImageCount": snapshot["media"].get("backgroundImageCount", 0),
            "canvasCount": snapshot["media"].get("canvasCount", 0),
            "videoCount": snapshot["media"]["videoCount"],
            "iframeCount": snapshot["media"]["iframeCount"],
            "sampleCount": len(snapshot["media"]["samples"]),
        },
    }


def format_browser_snapshot(payload: dict[str, Any]) -> str:
    snapshot = normalize_browser_snapshot(payload)
    lines = [
        "[browser_snapshot]",
        f"title: {snapshot['title'] or '(untitled)'}",
        f"url: {snapshot['url'] or '(unknown)'}",
    ]
    if snapshot["browser"]:
        lines.append(f"browser: {snapshot['browser']}")
    if snapshot.get("profile"):
        profile = snapshot["profile"]
        lines.append(f"profile: {profile.get('id')}")
        suppressed = profile.get("suppressed") or []
        if suppressed:
            lines.append("")
            lines.append("profile_notes:")
            for item in suppressed:
                lines.append(f"- suppressed {item['count']} {item['label']}")
    if snapshot["selection"]:
        lines.extend(["", "selection:", snapshot["selection"]])
    if snapshot["headings"]:
        lines.append("")
        lines.append("headings:")
        lines.extend(f"- {heading}" for heading in snapshot["headings"])
    if snapshot["nodes"]:
        lines.append("")
        lines.append("actionable_nodes:")
        for node in snapshot["nodes"]:
            label = node["name"] or node["text"] or node.get("href") or node["id"]
            actions = ",".join(node["actions"])
            suffix = f" actions={actions}" if actions else ""
            viewport = f" viewport={node['viewport']}" if node.get("viewport") else ""
            lines.append(f"- {node['id']} role={node['role']} label={label!r}{suffix}{viewport}")
    media = snapshot["media"]
    if media["imageCount"] or media["videoCount"] or media["iframeCount"]:
        lines.append("")
        lines.append("media_digest:")
        detail = []
        if media.get("backgroundImageCount"):
            detail.append(f"background_images={media['backgroundImageCount']}")
        if media.get("canvasCount"):
            detail.append(f"canvases={media['canvasCount']}")
        suffix = " " + " ".join(detail) if detail else ""
        lines.append(f"- images={media['imageCount']} videos={media['videoCount']} iframes={media['iframeCount']}{suffix}")
        for sample in media["samples"]:
            label = f" label={sample['label']!r}" if sample.get("label") else ""
            viewport = f" viewport={sample['viewport']}" if sample.get("viewport") else ""
            lines.append(f"- {sample['kind']}{label}{viewport}")
    if snapshot["textBlocks"]:
        lines.append("")
        lines.append("visible_text:")
        lines.extend(f"- {block}" for block in snapshot["textBlocks"])
    return "\n".join(lines).strip()


def strip_browser_action_markers(text: str) -> str:
    text = BROWSER_ACTION_RE.sub("", text or "")
    text = BROWSER_DONE_RE.sub("", text)
    return text.strip()


def strip_browser_done_markers(text: str) -> str:
    return BROWSER_DONE_RE.sub("", text or "").strip()


def _action_attrs(raw: str) -> dict[str, str]:
    return {key.lower().replace("-", "_"): unescape(value) for key, _quote, value in ACTION_ATTR_RE.findall(raw or "")}


def extract_browser_actions(text: str, payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    snapshot = normalize_browser_snapshot(payload)
    action_map = snapshot.get("actionMap") or {}
    actions: list[dict[str, Any]] = []
    for match in BROWSER_ACTION_RE.finditer(text or ""):
        attrs = _action_attrs(match.group(1) or match.group(2) or "")
        node_id = _clean_text(attrs.get("node") or attrs.get("node_id") or attrs.get("id"), limit=64)
        action = _clean_text(attrs.get("action") or "click", limit=32).lower()
        if action in {"type", "input"}:
            action = "set_text"
        if action == "goto":
            url = _clean_text(attrs.get("url") or attrs.get("href"), limit=2000)
            if not url or not url.lower().startswith(("http://", "https://")):
                continue
            actions.append({
                "nodeId": "",
                "action": "goto",
                "url": url,
                "selector": "",
                "role": "",
                "name": url,
            })
            if len(actions) >= 3:
                break
            continue
        if action == "scroll" and not node_id:
            dir_attr = _clean_text(attrs.get("dir") or attrs.get("direction") or "down", limit=16).lower()
            if dir_attr not in {"up", "down", "top", "bottom", "page"}:
                dir_attr = "down"
            actions.append({
                "nodeId": "",
                "action": "scroll",
                "dir": dir_attr,
                "selector": "",
                "role": "",
                "name": f"scroll {dir_attr}",
            })
            if len(actions) >= 3:
                break
            continue
        if action not in {"click", "set_text", "focus", "scroll", "key"}:
            continue
        target = action_map.get(node_id)
        if not isinstance(target, dict):
            continue
        selector = _clean_text(target.get("selector"), limit=360)
        if not selector:
            continue
        available = {str(item) for item in target.get("actions") or []}
        if action == "click" and "click" not in available:
            continue
        if action == "set_text" and "set_text" not in available:
            continue
        actions.append({
            "nodeId": node_id,
            "action": action,
            "text": _clean_text(attrs.get("text"), limit=500),
            "key": _clean_text(attrs.get("key"), limit=32) if action == "key" else "",
            "selector": selector,
            "role": _clean_text(target.get("role"), limit=48),
            "name": _clean_text(target.get("name"), limit=120),
        })
        if len(actions) >= 3:
            break
    return strip_browser_action_markers(text), actions


def extract_browser_done(text: str) -> tuple[str, dict[str, str] | None]:
    for match in BROWSER_DONE_RE.finditer(text or ""):
        attrs = _action_attrs(match.group(1) or match.group(2) or "")
        return strip_browser_done_markers(text), {
            "reason": _clean_text(attrs.get("reason") or attrs.get("message") or "done", limit=240),
        }
    return text or "", None


def build_browser_runtime_text(question: str, payload: dict[str, Any]) -> str:
    prompt = _clean_text(question, limit=2000)
    if not prompt:
        prompt = "请根据当前浏览器页面，给出有用的观察和下一步建议。"
    return "\n\n".join([
        format_browser_snapshot(payload),
        "[browser_action_protocol]\nOnly if the user explicitly asks you to operate the page, include at most one hidden XML action using a listed node id: <browser_action node=\"node_3\" action=\"click\" /> or <browser_action node=\"node_1\" action=\"set_text\" text=\"...\" />. Do not include this marker for observation-only answers.",
        "[user_request]",
        prompt,
    ])


def build_browser_control_text(payload: dict[str, Any]) -> str:
    reason = _clean_text(payload.get("reason"), limit=120) or "page_changed"
    parts = [
        format_browser_snapshot(payload),
        "[browser_control]\nThis browser tab is available for you to operate directly. You are not just observing it. Prefer taking exactly one low-risk browser operation now. Prefer current page content controls over global navigation. Do not wander between primary navigation items just to explore; after opening a navigation page, inspect/use page content or stop. Good idle first moves are focusing a visible search/text input, opening a clearly relevant content control, or choosing an obviously useful page control. Include exactly one hidden XML action using a listed node id: <browser_action node=\"node_3\" action=\"click\" /> or <browser_action node=\"node_1\" action=\"set_text\" text=\"...\" />. To open a different page directly, use <browser_action action=\"goto\" url=\"https://...\" /> (no node id; only when no listed control gets you there). To scroll, use <browser_action action=\"scroll\" dir=\"down\" /> (down|up|top|bottom). To submit a form after set_text, use <browser_action action=\"key\" node=\"node_1\" key=\"Enter\" />. Use set_text only when the intended text is clear; otherwise focus the relevant input. Do not repeat a recent action if the page did not materially change. When this control loop should stop, include exactly one hidden stop marker and no action: <browser_done reason=\"brief reason\" />. If every available operation is risky, repetitive, global-navigation wandering, or meaningless, stop with browser_done.",
    ]
    trail = _list_of_dicts(payload.get("controlTrail") or payload.get("control_trail"))[-6:]
    if trail:
        parts.append("[recent_browser_actions]")
        for item in trail:
            parts.append(f"- { _clean_text(item.get('action'), limit=24) } { _clean_text(item.get('nodeId') or item.get('node_id'), limit=64) } { _clean_text(item.get('name'), limit=120) } => { _clean_text(item.get('result'), limit=80) }")
    parts.extend(["[browser_event]", reason])
    return "\n\n".join(parts)