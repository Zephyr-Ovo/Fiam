"""CLI helpers for fiam plugin manifests."""

from __future__ import annotations

import argparse

from fiam_lib.core import _build_config


def cmd_plugin(args: argparse.Namespace) -> None:
    config = _build_config(args)
    from fiam.plugins import get_plugin, load_plugins, set_plugin_enabled

    action = args.plugin_action
    if action == "list":
        plugins = load_plugins(config)
        if not plugins:
            print("No plugins found.")
            return
        print(f"{'id':<18} {'enabled':<8} {'status':<14} {'kind':<12} name")
        for plugin in plugins:
            enabled = "yes" if plugin.enabled else "no"
            print(f"{plugin.id:<18} {enabled:<8} {plugin.status:<14} {plugin.kind:<12} {plugin.name}")
        return

    if action == "show":
        plugin = get_plugin(config, args.plugin_id)
        if plugin is None:
            raise SystemExit(f"Plugin not found: {args.plugin_id}")
        print(f"id: {plugin.id}")
        print(f"name: {plugin.name}")
        print(f"enabled: {str(plugin.enabled).lower()}")
        print(f"status: {plugin.status}")
        print(f"kind: {plugin.kind}")
        if plugin.description:
            print(f"description: {plugin.description}")
        if plugin.receive_sources:
            print(f"receive_sources: {', '.join(plugin.receive_sources)}")
        if plugin.dispatch_targets:
            print(f"dispatch_targets: {', '.join(plugin.dispatch_targets)}")
        if plugin.transports:
            print(f"transports: {', '.join(plugin.transports)}")
        if plugin.capabilities:
            print(f"capabilities: {', '.join(plugin.capabilities)}")
        if plugin.entrypoint:
            print(f"entrypoint: {plugin.entrypoint}")
        if plugin.auth:
            print(f"auth: {plugin.auth}")
        if plugin.latency:
            print(f"latency: {plugin.latency}")
        if plugin.env:
            print(f"env: {', '.join(plugin.env)}")
        if plugin.replaces:
            print(f"replaces: {', '.join(plugin.replaces)}")
        if plugin.notes:
            print("notes:")
            for note in plugin.notes:
                print(f"  - {note}")
        print(f"manifest: {plugin.manifest_path}")
        return

    enabled = action == "enable"
    plugin = set_plugin_enabled(config, args.plugin_id, enabled)
    state = "enabled" if plugin.enabled else "disabled"
    print(f"{plugin.id}: {state}")