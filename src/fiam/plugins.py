"""Plugin manifest registry for optional fiam integrations.

Plugins are intentionally small: a directory under ``plugins/<id>/`` with a
``plugin.toml`` manifest. Runtime code can ask whether a receive channel or
dispatch target is enabled without knowing how the plugin is implemented.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    enabled: bool
    status: str
    kind: str
    description: str
    transports: tuple[str, ...]
    capabilities: tuple[str, ...]
    receive_channels: tuple[str, ...]
    dispatch_targets: tuple[str, ...]
    entrypoint: str
    auth: str
    latency: str
    env: tuple[str, ...]
    replaces: tuple[str, ...]
    notes: tuple[str, ...]
    delivery: str  # "instant" | "lazy"
    path: Path
    raw: dict[str, Any] = field(repr=False, compare=False)

    @property
    def manifest_path(self) -> Path:
        return self.path / "plugin.toml"


def plugin_root(config_or_path: Any) -> Path:
    """Return the plugin root directory for a config or repository path."""
    if hasattr(config_or_path, "code_path"):
        return Path(config_or_path.code_path) / "plugins"
    return Path(config_or_path) / "plugins"


def load_plugins(config_or_path: Any) -> list[PluginManifest]:
    """Load all plugin manifests, sorted by id."""
    root = plugin_root(config_or_path)
    if not root.exists():
        return []
    plugins: list[PluginManifest] = []
    for manifest_path in sorted(root.glob("*/plugin.toml")):
        try:
            plugins.append(load_plugin_manifest(manifest_path))
        except Exception:
            continue
    plugins.sort(key=lambda item: item.id)
    return plugins


def load_plugin_manifest(manifest_path: Path) -> PluginManifest:
    raw = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    disabled_by_path = manifest_path.parent.name.endswith(".disabled")
    plugin_id = str(raw.get("id") or manifest_path.parent.name.removesuffix(".disabled"))
    enabled = bool(raw.get("enabled", True)) and not disabled_by_path
    return PluginManifest(
        id=plugin_id,
        name=str(raw.get("name", plugin_id)),
        enabled=enabled,
        status=str(raw.get("status", "active")),
        kind=str(raw.get("kind", "integration")),
        description=str(raw.get("description", "")),
        transports=_tuple(raw.get("transports")),
        capabilities=_tuple(raw.get("capabilities")),
        receive_channels=_tuple(raw.get("receive_channels")),
        dispatch_targets=_tuple(raw.get("dispatch_targets")),
        entrypoint=str(raw.get("entrypoint", "")),
        auth=str(raw.get("auth", "")),
        latency=str(raw.get("latency", "")),
        env=_tuple(raw.get("env")),
        replaces=_tuple(raw.get("replaces")),
        notes=_tuple(raw.get("notes")),
        delivery=str(raw.get("delivery", "instant")).strip().lower() or "instant",
        path=manifest_path.parent,
        raw=raw,
    )


def get_plugin(config_or_path: Any, plugin_id: str) -> PluginManifest | None:
    plugin_id = plugin_id.strip().lower()
    for plugin in load_plugins(config_or_path):
        if plugin.id.lower() == plugin_id:
            return plugin
    return None


def plugin_for_receive(config_or_path: Any, channel: str) -> PluginManifest | None:
    channel = channel.strip().lower()
    for plugin in load_plugins(config_or_path):
        if channel in {item.lower() for item in plugin.receive_channels}:
            return plugin
    return None


def plugin_for_dispatch(config_or_path: Any, target: str) -> PluginManifest | None:
    target = target.strip().lower()
    for plugin in load_plugins(config_or_path):
        if target in {item.lower() for item in plugin.dispatch_targets}:
            return plugin
    return None


def is_receive_enabled(config_or_path: Any, channel: str, *, default: bool = True) -> bool:
    plugin = plugin_for_receive(config_or_path, channel)
    return default if plugin is None else plugin.enabled


def delivery_for_channel(config_or_path: Any, channel: str, *, default: str = "instant") -> str:
    """Return delivery mode for `channel`: "instant" (wakes AI) or "lazy" (inbox-only).

    Lazy channels only land in events + notifications/inbox/ and wait for
    AI to look on its own time. Channels without a matching plugin manifest
    fall back to `default`.
    """
    plugin = plugin_for_receive(config_or_path, channel)
    return default if plugin is None else (plugin.delivery or default)


def is_dispatch_enabled(config_or_path: Any, target: str, *, default: bool = True) -> bool:
    plugin = plugin_for_dispatch(config_or_path, target)
    return default if plugin is None else plugin.enabled


def resolve_dispatch_target(config_or_path: Any, target: str) -> str | None:
    """Resolve a marker/channel alias to its canonical dispatch topic leaf.

    Returns None when a known plugin exists but is disabled. Unknown targets are
    returned unchanged so experiments can still publish to ad-hoc topics.
    """
    target = target.strip().lower()
    plugin = plugin_for_dispatch(config_or_path, target)
    if plugin is None:
        return target
    if not plugin.enabled:
        return None
    return plugin.dispatch_targets[0] if plugin.dispatch_targets else plugin.id


def enabled_dispatch_targets(config_or_path: Any) -> set[str]:
    out: set[str] = set()
    for plugin in load_plugins(config_or_path):
        if plugin.enabled:
            out.update(plugin.dispatch_targets)
    return out


def set_plugin_enabled(config_or_path: Any, plugin_id: str, enabled: bool) -> PluginManifest:
    """Rewrite the manifest's top-level ``enabled =`` line."""
    plugin = get_plugin(config_or_path, plugin_id)
    if plugin is None:
        raise KeyError(f"plugin not found: {plugin_id}")
    manifest = plugin.manifest_path
    text = manifest.read_text(encoding="utf-8")
    lines = text.splitlines()
    value = "true" if enabled else "false"
    replaced = False
    for i, line in enumerate(lines):
        if re.match(r"^\s*enabled\s*=", line):
            lines[i] = f"enabled = {value}"
            replaced = True
            break
    if not replaced:
        insert_at = 1 if lines else 0
        lines.insert(insert_at, f"enabled = {value}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_plugin_manifest(manifest)


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return (str(value),)
