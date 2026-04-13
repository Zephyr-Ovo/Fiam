"""
Central configuration for fiam.

All paths derive from two roots:
  - home_path  — AI's home directory (human-readable .md only)
  - code_path  — infrastructure (events, embeddings, code, logs)

Naming convention: home_path is the AI's territory.
  code_path is the "basement" — machine data, never visited manually.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


# ------------------------------------------------------------------
# Language profile presets
# ------------------------------------------------------------------
# Each profile defines which models to download and use.
# "zh"    — Chinese-focused: bge-zh embedding, Chinese-Emotion (8 labels)
# "en"    — English-focused: bge-en embedding, GoEmotions (28 labels)
# "multi" — Multilingual: bge-m3 embedding, auto-detect → zh or en emotion model

from typing import Any

# Chinese emotion model variants (same 8-class labels, different backbones)
EMOTION_ZH_MODELS: dict[str, dict[str, str | int]] = {
    "small": {
        "name": "Johnson8187/Chinese-Emotion-Small",
        "backbone": "mDeBERTa-v3-base",
        "size_mb": 300,
    },
    "large": {
        "name": "Johnson8187/Chinese-Emotion",
        "backbone": "xlm-roberta-large",
        "size_mb": 2240,
    },
}

LANGUAGE_PROFILES: dict[str, dict[str, Any]] = {
    "zh": {
        "embedding": "BAAI/bge-base-zh-v1.5",
        "embedding_dim": 768,
        "emotion_zh": "Johnson8187/Chinese-Emotion-Small",
        "emotion_en": "",
    },
    "en": {
        "embedding": "BAAI/bge-base-en-v1.5",
        "embedding_dim": 768,
        "emotion_zh": "",
        "emotion_en": "SamLowe/roberta-base-go_emotions",
    },
    "multi": {
        "embedding": "BAAI/bge-m3",
        "embedding_dim": 1024,
        "emotion_zh": "Johnson8187/Chinese-Emotion-Small",
        "emotion_en": "SamLowe/roberta-base-go_emotions",
    },
}


@dataclass
class FiamConfig:
    # ------------------------------------------------------------------
    # Two root paths (set by CLI or caller)
    # ------------------------------------------------------------------
    home_path: Path           # AI's home — passed as --home CLI arg
    code_path: Path           # fiam-code root — auto-detected

    # ------------------------------------------------------------------
    # All registered home directories
    # ------------------------------------------------------------------
    home_paths: list[Path] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Identity (user-configurable for open-source)
    # ------------------------------------------------------------------
    ai_name: str = ""
    user_name: str = ""

    # ------------------------------------------------------------------
    # Language profile  ("zh" | "en" | "multi")
    # ------------------------------------------------------------------
    language_profile: str = "multi"

    # ------------------------------------------------------------------
    # Emotion provider  ("local" = WDI models | "api" = LLM API)
    # ------------------------------------------------------------------
    emotion_provider: str = "local"

    # ------------------------------------------------------------------
    # Emotion models (only used when emotion_provider == "local")
    # ------------------------------------------------------------------
    emotion_model_zh: str = ""   # Chinese emotion: Johnson8187/Chinese-Emotion-Small
    emotion_model_en: str = ""   # English emotion: SamLowe/roberta-base-go_emotions

    # ------------------------------------------------------------------
    # Embedding model (single model, derived from language_profile)
    # ------------------------------------------------------------------
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    # ------------------------------------------------------------------
    # Embedding backend  ("local" = in-process | "remote" = API server)
    # ------------------------------------------------------------------
    embedding_backend: str = "local"
    embedding_remote_url: str = ""  # e.g. "http://127.0.0.1:8819" (via SSH tunnel)

    # ------------------------------------------------------------------
    # Retrieval weights (must sum to 1.0)
    # ------------------------------------------------------------------
    semantic_weight: float = 0.5
    recency_weight: float = 0.3
    temporal_link_weight: float = 0.2

    # ------------------------------------------------------------------
    # Retrieval parameters
    # ------------------------------------------------------------------
    top_k: int = 5                      # max events retrieved per pre-session
    diversity_threshold: float = 0.88   # cosine similarity ceiling for dedup
    min_event_age_hours: int = 6        # skip events newer than this
    temporal_window_hours: float = 4.0  # legacy; kept for toml compat (unused)

    # ------------------------------------------------------------------
    # Event extraction parameters
    # ------------------------------------------------------------------
    arousal_threshold: float = 0.6      # storage gate: pairs below this are discarded
    half_life_base: float = 14.0        # Ebbinghaus base half-life in days (strength=1.0)

    # ------------------------------------------------------------------
    # Narrative synthesis
    # ------------------------------------------------------------------
    narrative_llm_enabled: bool = False  # False = rule-based only, no API call
    narrative_llm_provider: str = "anthropic"  # "anthropic" | "openai" (compatible)
    narrative_llm_model: str = "claude-3-5-haiku-20241022"
    narrative_llm_base_url: str = ""     # for openai-compatible providers
    narrative_llm_api_key_env: str = ""  # env var name; empty = provider default

    # ------------------------------------------------------------------
    # Event ID prefix
    # ------------------------------------------------------------------
    event_id_prefix: str = "ev"

    # ------------------------------------------------------------------
    # Graph edge typing (LLM-based edge classification + event naming)
    # ------------------------------------------------------------------
    graph_edge_provider: str = ""      # "deepseek" | "openai" | "" (disabled)
    graph_edge_model: str = "deepseek-chat"
    graph_edge_base_url: str = "https://api.deepseek.com"
    graph_edge_api_key_env: str = "FIAM_GRAPH_API_KEY"

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------
    debug_mode: bool = False

    # ------------------------------------------------------------------
    # Daemon
    # ------------------------------------------------------------------
    idle_timeout_minutes: int = 30      # minutes of no JSONL activity → trigger processing
    poll_interval_seconds: int = 30     # how often to check JSONL mtime

    # ------------------------------------------------------------------
    # Communication (outbox dispatch)
    # ------------------------------------------------------------------
    tg_bot_token_env: str = "FIAM_TG_BOT_TOKEN"  # env var holding Telegram Bot token
    tg_chat_id: str = ""                          # Telegram chat ID for user
    email_from: str = ""                           # AI's address (e.g. Fiet.C@proton.me)
    email_to: str = ""                             # User's address
    email_smtp_host: str = ""                      # SMTP host (e.g. 127.0.0.1 for ProtonMail Bridge)
    email_smtp_port: int = 1025

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------
    git_enabled: bool = True

    def __post_init__(self) -> None:
        """Apply language profile defaults for unset model fields."""
        # Ensure home_paths list is in sync with home_path
        if not self.home_paths:
            self.home_paths = [self.home_path]
        elif self.home_path not in self.home_paths:
            self.home_paths.insert(0, self.home_path)

        profile = LANGUAGE_PROFILES.get(self.language_profile, LANGUAGE_PROFILES["multi"])
        if not self.embedding_model:
            self.embedding_model = str(profile["embedding"])
        if not self.embedding_dim:
            self.embedding_dim = int(profile["embedding_dim"])
        if self.emotion_provider == "local":
            if not self.emotion_model_zh:
                self.emotion_model_zh = str(profile.get("emotion_zh", ""))
            if not self.emotion_model_en:
                self.emotion_model_en = str(profile.get("emotion_en", ""))
        else:
            # API mode — no local emotion models needed
            self.emotion_model_zh = ""
            self.emotion_model_en = ""

    # ------------------------------------------------------------------
    # Derived paths — code side (store/ = the "basement")
    # ------------------------------------------------------------------

    @property
    def store_dir(self) -> Path:
        return self.code_path / "store"

    @property
    def events_dir(self) -> Path:
        return self.store_dir / "events"

    @property
    def embeddings_dir(self) -> Path:
        return self.store_dir / "embeddings"

    @property
    def graph_jsonl_path(self) -> Path:
        return self.store_dir / "graph.jsonl"

    @property
    def narrative_cache_path(self) -> Path:
        return self.store_dir / "narrative_cache.json"

    @property
    def logs_dir(self) -> Path:
        return self.code_path / "logs"

    # ------------------------------------------------------------------
    # Derived paths — home side (AI's home, human-readable only)
    # ------------------------------------------------------------------

    @property
    def background_path(self) -> Path:
        return self.home_path / "recall.md"

    @property
    def claude_md_path(self) -> Path:
        return self.home_path / "CLAUDE.md"

    @property
    def self_dir(self) -> Path:
        """AI's private space — personality, journal, inner thoughts."""
        return self.home_path / "self"

    @property
    def personality_path(self) -> Path:
        return self.self_dir / "personality.md"

    @property
    def journal_dir(self) -> Path:
        return self.self_dir / "journal"

    @property
    def user_space_dir(self) -> Path:
        return self.home_path / self.user_name.lower()

    @property
    def outbox_dir(self) -> Path:
        return self.home_path / "outbox"

    @property
    def outbox_sent_dir(self) -> Path:
        return self.home_path / "outbox" / "sent"

    @property
    def inbox_dir(self) -> Path:
        return self.home_path / "inbox"

    @property
    def world_dir(self) -> Path:
        return self.home_path / "world"

    @property
    def schedule_path(self) -> Path:
        return self.self_dir / "schedule.jsonl"

    # ------------------------------------------------------------------
    # Directory creation
    # ------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        """Create all required directories if they don't exist."""
        for d in (
            # Code side
            self.events_dir,
            self.embeddings_dir,
            self.logs_dir,
            # Home side
            self.self_dir,
            self.journal_dir,
            self.user_space_dir,
            self.outbox_dir,
            self.outbox_sent_dir,
            self.inbox_dir,
            self.world_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # TOML persistence
    # ------------------------------------------------------------------

    @property
    def toml_path(self) -> Path:
        return self.code_path / "fiam.toml"

    def to_toml(self, path: Path | None = None) -> None:
        """Serialize user-configurable fields to a TOML file."""
        # Build home_paths TOML array
        paths_list = ", ".join(f'"{p.as_posix()}"' for p in self.home_paths)
        lines = [
            f'home_path = "{self.home_path.as_posix()}"',
            f'home_paths = [{paths_list}]',
            f'ai_name = "{self.ai_name}"',
            f'user_name = "{self.user_name}"',
            f'language_profile = "{self.language_profile}"',
            f'emotion_provider = "{self.emotion_provider}"',
            "",
            "[models]",
            f'embedding = "{self.embedding_model}"',
            f'emotion_zh = "{self.emotion_model_zh}"',
            f'emotion_en = "{self.emotion_model_en}"',
            f"embedding_dim = {self.embedding_dim}",
            f'embedding_backend = "{self.embedding_backend}"',
            f'embedding_remote_url = "{self.embedding_remote_url}"',
            "",
            "[retrieval]",
            f"top_k = {self.top_k}",
            f"semantic_weight = {self.semantic_weight}",
            f"recency_weight = {self.recency_weight}",
            f"temporal_link_weight = {self.temporal_link_weight}",
            f"diversity_threshold = {self.diversity_threshold}",
            f"min_event_age_hours = {self.min_event_age_hours}",
            f"temporal_window_hours = {self.temporal_window_hours}",
            "",
            "[extraction]",
            f"arousal_threshold = {self.arousal_threshold}",
            f"half_life_base = {self.half_life_base}",
            "",
            "[narrative]",
            f"llm_enabled = {str(self.narrative_llm_enabled).lower()}",
            f'llm_provider = "{self.narrative_llm_provider}"',
            f'llm_model = "{self.narrative_llm_model}"',
            f'llm_base_url = "{self.narrative_llm_base_url}"',
            f'llm_api_key_env = "{self.narrative_llm_api_key_env}"',
            "",
            "[daemon]",
            f"idle_timeout_minutes = {self.idle_timeout_minutes}",
            f"poll_interval_seconds = {self.poll_interval_seconds}",
            "",
            "[features]",
            f"git_enabled = {str(self.git_enabled).lower()}",
            "",
            "[graph]",
            f'edge_provider = "{self.graph_edge_provider}"',
            f'edge_model = "{self.graph_edge_model}"',
            f'edge_base_url = "{self.graph_edge_base_url}"',
            f'edge_api_key_env = "{self.graph_edge_api_key_env}"',
            "",
            "[comms]",
            f'tg_bot_token_env = "{self.tg_bot_token_env}"',
            f'tg_chat_id = "{self.tg_chat_id}"',
            f'email_from = "{self.email_from}"',
            f'email_to = "{self.email_to}"',
            f'email_smtp_host = "{self.email_smtp_host}"',
            f"email_smtp_port = {self.email_smtp_port}",
        ]
        dest = path or self.toml_path
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def from_toml(cls, toml_path: Path, code_path: Path) -> FiamConfig:
        """Load a FiamConfig from a TOML file."""
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))

        home_path = Path(raw["home_path"])
        home_paths_raw = raw.get("home_paths", [])
        home_paths = [Path(p) for p in home_paths_raw] if home_paths_raw else [home_path]
        models = raw.get("models", {})
        retrieval = raw.get("retrieval", {})
        extraction = raw.get("extraction", {})
        narrative = raw.get("narrative", {})
        daemon = raw.get("daemon", {})
        features = raw.get("features", {})
        comm = raw.get("communication", raw.get("comms", {}))
        graph = raw.get("graph", {})

        return cls(
            home_path=home_path,
            home_paths=home_paths,
            code_path=code_path,
            ai_name=raw.get("ai_name", ""),
            user_name=raw.get("user_name", ""),
            language_profile=raw.get("language_profile", "multi"),
            emotion_provider=raw.get("emotion_provider", "local"),
            # Models
            emotion_model_zh=models.get("emotion_zh", ""),
            emotion_model_en=models.get("emotion_en", ""),
            embedding_model=models.get("embedding",
                                       # backward compat: old toml had embedding_zh/embedding_en
                                       models.get("embedding_zh", "")),
            embedding_dim=models.get("embedding_dim", 0),  # 0 = derive from profile
            embedding_backend=models.get("embedding_backend", "local"),
            embedding_remote_url=models.get("embedding_remote_url", ""),
            # Retrieval
            top_k=retrieval.get("top_k", cls.top_k),
            semantic_weight=retrieval.get("semantic_weight", cls.semantic_weight),
            recency_weight=retrieval.get("recency_weight", cls.recency_weight),
            temporal_link_weight=retrieval.get("temporal_link_weight", cls.temporal_link_weight),
            diversity_threshold=retrieval.get("diversity_threshold", cls.diversity_threshold),
            min_event_age_hours=retrieval.get("min_event_age_hours", cls.min_event_age_hours),
            temporal_window_hours=retrieval.get("temporal_window_hours", cls.temporal_window_hours),
            # Extraction
            arousal_threshold=extraction.get("arousal_threshold", cls.arousal_threshold),
            half_life_base=extraction.get("half_life_base", cls.half_life_base),
            # Narrative
            narrative_llm_enabled=narrative.get("llm_enabled", cls.narrative_llm_enabled),
            narrative_llm_provider=narrative.get("llm_provider", cls.narrative_llm_provider),
            narrative_llm_model=narrative.get("llm_model", cls.narrative_llm_model),
            narrative_llm_base_url=narrative.get("llm_base_url", cls.narrative_llm_base_url),
            narrative_llm_api_key_env=narrative.get("llm_api_key_env", cls.narrative_llm_api_key_env),
            # Daemon
            idle_timeout_minutes=daemon.get("idle_timeout_minutes", cls.idle_timeout_minutes),
            poll_interval_seconds=daemon.get("poll_interval_seconds", cls.poll_interval_seconds),
            # Features
            git_enabled=features.get("git_enabled", cls.git_enabled),
            # Graph edge typing
            graph_edge_provider=graph.get("edge_provider", cls.graph_edge_provider),
            graph_edge_model=graph.get("edge_model", cls.graph_edge_model),
            graph_edge_base_url=graph.get("edge_base_url", cls.graph_edge_base_url),
            graph_edge_api_key_env=graph.get("edge_api_key_env", cls.graph_edge_api_key_env),
            # Communication
            tg_bot_token_env=comm.get("tg_bot_token_env", cls.tg_bot_token_env),
            tg_chat_id=str(comm.get("tg_chat_id", "")),
            email_from=comm.get("email_from", ""),
            email_to=comm.get("email_to", ""),
            email_smtp_host=comm.get("email_smtp_host", ""),
            email_smtp_port=comm.get("email_smtp_port", cls.email_smtp_port),
        )
