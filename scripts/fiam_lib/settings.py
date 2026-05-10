"""fiam settings — interactive config editor."""

from __future__ import annotations

import argparse
import sys

from fiam_lib.core import _project_root, _toml_path


# Editable fields grouped by section.
# (toml_section, field_name, config_attr, display_label, type)
_FIELDS = [
    ("",          "home_path",        "home_path",              "Home directory",                "path"),
    ("",          "user_name",        "user_name",              "Your name",                     "str"),
    ("",          "language_profile",  "language_profile",      "Language profile (zh/en/multi)", "str"),
    ("retrieval", "top_k",            "top_k",                  "Retrieval top_k",               "int"),
    ("retrieval", "semantic_weight",  "semantic_weight",        "Semantic weight",               "float"),
    ("retrieval", "recency_weight",   "recency_weight",        "Recency weight",                "float"),
    ("retrieval", "min_event_age_hours","min_event_age_hours",  "Min event age (hours)",         "int"),
    ("narrative", "llm_enabled",       "narrative_llm_enabled", "Narrative LLM enabled",         "bool"),
    ("narrative", "llm_provider",      "narrative_llm_provider","LLM provider",                  "str"),
    ("narrative", "llm_model",         "narrative_llm_model",   "LLM model",                     "str"),
    ("narrative", "llm_base_url",      "narrative_llm_base_url","LLM base URL",                  "str"),
    ("narrative", "llm_api_key_env",   "narrative_llm_api_key_env","LLM API key env var",        "str"),
    ("app",       "cot_summary_enabled","app_cot_summary_enabled","CoT summary enabled",        "bool"),
    ("app",       "cot_summary_model",  "app_cot_summary_model", "CoT summary model",            "str"),
    ("app",       "cot_summary_base_url","app_cot_summary_base_url","CoT summary base URL",      "str"),
    ("app",       "cot_summary_api_key_env","app_cot_summary_api_key_env","CoT summary API key env var","str"),
    ("daemon",    "idle_timeout_minutes","idle_timeout_minutes", "Idle timeout (minutes)",       "int"),
    ("daemon",    "poll_interval_seconds","poll_interval_seconds","Poll interval (seconds)",     "int"),
]


def cmd_settings(args: argparse.Namespace) -> None:
    """Show and edit fiam configuration."""
    from fiam.config import FiamConfig

    code_path = _project_root()
    toml = _toml_path()

    if not toml.exists():
        print("  No fiam.toml found. Run 'fiam init' first.", file=sys.stderr)
        sys.exit(1)

    config = FiamConfig.from_toml(toml, code_path)

    # If --set key=value provided, apply directly
    set_pairs = getattr(args, "set", None)
    if set_pairs:
        _apply_sets(config, set_pairs)
        config.to_toml()
        print("  ✓ Updated fiam.toml")
        return

    # Interactive mode — show all, prompt for changes
    print()
    print("  fiam settings")
    print("  ─────────────────────────────────────")
    print()

    changed = False
    current_section = ""

    for section, _key, attr, label, ftype in _FIELDS:
        if section != current_section:
            if section:
                print(f"  [{section}]")
            current_section = section

        val = getattr(config, attr)
        if ftype == "path":
            val = str(val)

        prompt = f"  {label} [{val}]: "
        user_input = input(prompt).strip()

        if not user_input:
            continue  # keep current value

        try:
            new_val = _parse_value(user_input, ftype)
        except ValueError as e:
            print(f"    ⚠ Invalid value: {e}")
            continue

        if ftype == "path":
            from pathlib import Path
            setattr(config, attr, Path(new_val))
        else:
            setattr(config, attr, new_val)
        changed = True

    if changed:
        config.to_toml()
        print()
        print("  ✓ Settings saved to fiam.toml")
    else:
        print()
        print("  (no changes)")
    print()


def _apply_sets(config, pairs: list[str]) -> None:
    """Apply key=value pairs from --set arguments."""
    attr_map = {attr: (label, ftype) for _, _, attr, label, ftype in _FIELDS}
    # Also allow short names (field_name from toml)
    key_map = {key: attr for _, key, attr, _, _ in _FIELDS}

    for pair in pairs:
        if "=" not in pair:
            print(f"  ⚠ Invalid format: {pair} (use key=value)", file=sys.stderr)
            continue
        key, val = pair.split("=", 1)
        key = key.strip()
        val = val.strip()

        # Resolve key to attribute
        attr = key_map.get(key, key)
        if attr not in attr_map:
            print(f"  ⚠ Unknown setting: {key}", file=sys.stderr)
            continue

        label, ftype = attr_map[attr]
        try:
            new_val = _parse_value(val, ftype)
        except ValueError as e:
            print(f"  ⚠ {key}: {e}", file=sys.stderr)
            continue

        if ftype == "path":
            from pathlib import Path
            setattr(config, attr, Path(new_val))
        else:
            setattr(config, attr, new_val)
        print(f"  {label} → {new_val}")


def _parse_value(raw: str, ftype: str):
    """Parse a string value into the correct type."""
    if ftype == "int":
        return int(raw)
    elif ftype == "float":
        return float(raw)
    elif ftype == "bool":
        return raw.lower() in ("true", "1", "yes", "on")
    elif ftype == "path":
        return raw
    else:
        return raw
