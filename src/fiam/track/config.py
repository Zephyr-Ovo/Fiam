from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TrackConfig:
    model: str = ""
    api_env: str = "MIMO_API_KEY"
    endpoint: str = ""
    vault_dir: Path = Path()
    code_dir: Path = Path()
    store_dir: Path = Path()

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_env, "").strip()

    @property
    def llm_ready(self) -> bool:
        return bool(self.model and self.endpoint and self.api_key)


def _resolve_vault_dir() -> Path:
    override = os.environ.get("FIAM_STUDIO_VAULT_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    home = os.environ.get("FIAM_HOME", "").strip()
    if home:
        return (Path(home).expanduser() / "studio").resolve()
    return (Path.home() / "studio").resolve()


def _resolve_code_dir() -> Path:
    override = os.environ.get("FIAM_CODE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    home = os.environ.get("FIAM_HOME", "").strip()
    if home:
        return (Path(home).expanduser() / "fiam-code").resolve()
    return (Path.home() / "fiam-code").resolve()


def _resolve_store_dir() -> Path:
    override = os.environ.get("FIAM_STORE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    home = os.environ.get("FIAM_HOME", "").strip()
    if home:
        return (Path(home).expanduser() / "fiam-code" / "store").resolve()
    return (Path.home() / "fiam-code" / "store").resolve()


def load_track_config(toml_path: Path | None = None) -> TrackConfig:
    """Load [track] section from fiam.toml; env vars override individual fields."""
    section: dict = {}
    if toml_path and toml_path.exists():
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        if isinstance(raw.get("track"), dict):
            section = raw["track"]
    return TrackConfig(
        model=os.environ.get("FIAM_TRACK_MODEL", section.get("model", "")).strip(),
        api_env=os.environ.get("FIAM_TRACK_API_ENV", section.get("api_env", "MIMO_API_KEY")).strip()
        or "MIMO_API_KEY",
        endpoint=os.environ.get("FIAM_TRACK_ENDPOINT", section.get("endpoint", "")).strip(),
        vault_dir=_resolve_vault_dir(),
        code_dir=_resolve_code_dir(),
        store_dir=_resolve_store_dir(),
    )
