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
# "zh"    — Chinese-focused: bge-zh embedding, cardiffnlp for valence+arousal
# "en"    — English-focused: bge-en embedding, j-hartmann for fine-grained arousal
# "multi" — Multilingual: single bge-m3 embedding (unified vector space), cardiffnlp

from typing import Any

LANGUAGE_PROFILES: dict[str, dict[str, Any]] = {
    "zh": {
        "embedding": "BAAI/bge-base-zh-v1.5",
        "embedding_dim": 768,
        "emotion": "",  # not used — arousal from cardiffnlp proxy
        "sentiment": "cardiffnlp/twitter-xlm-roberta-base-sentiment",
    },
    "en": {
        "embedding": "BAAI/bge-base-en-v1.5",
        "embedding_dim": 768,
        "emotion": "j-hartmann/emotion-english-distilroberta-base",
        "sentiment": "cardiffnlp/twitter-xlm-roberta-base-sentiment",
    },
    "multi": {
        "embedding": "BAAI/bge-m3",
        "embedding_dim": 1024,
        "emotion": "",  # not used — arousal from cardiffnlp proxy
        "sentiment": "cardiffnlp/twitter-xlm-roberta-base-sentiment",
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
    # Identity (user-configurable for open-source)
    # ------------------------------------------------------------------
    ai_name: str = ""
    user_name: str = ""

    # ------------------------------------------------------------------
    # Language profile  ("zh" | "en" | "multi")
    # ------------------------------------------------------------------
    language_profile: str = "multi"

    # ------------------------------------------------------------------
    # Classifier models (derived from language_profile, overridable)
    # ------------------------------------------------------------------
    emotion_model_name: str = ""   # empty = use cardiffnlp proxy for arousal
    sentiment_model_name: str = "cardiffnlp/twitter-xlm-roberta-base-sentiment"

    # ------------------------------------------------------------------
    # Embedding model (single model, derived from language_profile)
    # ------------------------------------------------------------------
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

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
    temporal_window_hours: float = 4.0  # linking window for causal co-occurrence

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
    # Debug
    # ------------------------------------------------------------------
    debug_mode: bool = False

    # ------------------------------------------------------------------
    # Daemon
    # ------------------------------------------------------------------
    idle_timeout_minutes: int = 30      # minutes of no JSONL activity → trigger processing
    poll_interval_seconds: int = 30     # how often to check JSONL mtime

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------
    git_enabled: bool = True

    def __post_init__(self) -> None:
        """Apply language profile defaults for unset model fields."""
        profile = LANGUAGE_PROFILES.get(self.language_profile, LANGUAGE_PROFILES["multi"])
        if not self.embedding_model:
            self.embedding_model = str(profile["embedding"])
        if not self.embedding_dim:
            self.embedding_dim = int(profile["embedding_dim"])
        if not self.sentiment_model_name:
            self.sentiment_model_name = str(profile["sentiment"])
        # emotion_model_name: empty string = use cardiffnlp proxy for arousal
        # Only set from profile if not explicitly overridden
        if self.emotion_model_name is None:
            self.emotion_model_name = str(profile["emotion"])

    @property
    def use_emotion_model(self) -> bool:
        """Whether a dedicated emotion model is configured (en profile)."""
        return bool(self.emotion_model_name)

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
        lines = [
            f'home_path = "{self.home_path.as_posix()}"',
            f'ai_name = "{self.ai_name}"',
            f'user_name = "{self.user_name}"',
            f'language_profile = "{self.language_profile}"',
            "",
            "[models]",
            f'embedding = "{self.embedding_model}"',
            f'sentiment = "{self.sentiment_model_name}"',
            f'emotion = "{self.emotion_model_name}"',
            f"embedding_dim = {self.embedding_dim}",
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
            "[daemon]",
            f"idle_timeout_minutes = {self.idle_timeout_minutes}",
            f"poll_interval_seconds = {self.poll_interval_seconds}",
            "",
            "[features]",
            f"git_enabled = {str(self.git_enabled).lower()}",
        ]
        dest = path or self.toml_path
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def from_toml(cls, toml_path: Path, code_path: Path) -> FiamConfig:
        """Load a FiamConfig from a TOML file."""
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))

        home_path = Path(raw["home_path"])
        models = raw.get("models", {})
        retrieval = raw.get("retrieval", {})
        extraction = raw.get("extraction", {})
        daemon = raw.get("daemon", {})
        features = raw.get("features", {})

        return cls(
            home_path=home_path,
            code_path=code_path,
            ai_name=raw.get("ai_name", ""),
            user_name=raw.get("user_name", ""),
            language_profile=raw.get("language_profile", "multi"),
            # Models
            emotion_model_name=models.get("emotion", ""),
            sentiment_model_name=models.get("sentiment", cls.sentiment_model_name),
            embedding_model=models.get("embedding",
                                       # backward compat: old toml had embedding_zh/embedding_en
                                       models.get("embedding_zh", "")),
            embedding_dim=models.get("embedding_dim", 0),  # 0 = derive from profile
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
            # Daemon
            idle_timeout_minutes=daemon.get("idle_timeout_minutes", cls.idle_timeout_minutes),
            poll_interval_seconds=daemon.get("poll_interval_seconds", cls.poll_interval_seconds),
            # Features
            git_enabled=features.get("git_enabled", cls.git_enabled),
        )
