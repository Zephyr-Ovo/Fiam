"""
Central configuration for fiam.

All paths derive from two roots:
  - home_path  — AI's home directory (human-readable .md only)
    - code_path  — infrastructure (pool, features, code, logs)

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
# Each profile defines which embedding model to use.
# "zh"    — Chinese-focused: bge-zh embedding
# "en"    — English-focused: bge-en embedding
# "multi" — Multilingual: bge-m3 embedding (unified vector space)

from typing import Any

LANGUAGE_PROFILES: dict[str, dict[str, Any]] = {
    "zh": {
        "embedding": "BAAI/bge-base-zh-v1.5",
        "embedding_dim": 768,
    },
    "en": {
        "embedding": "BAAI/bge-base-en-v1.5",
        "embedding_dim": 768,
    },
    "multi": {
        "embedding": "BAAI/bge-m3",
        "embedding_dim": 1024,
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
    top_k: int = 3                      # safety cap: max events per retrieval
    min_score: float = 0.15             # primary gate: skip events below this combined score
    diversity_threshold: float = 0.88   # cosine similarity ceiling for dedup
    min_event_age_hours: int = 6        # skip events newer than this
    temporal_window_hours: float = 4.0  # legacy; kept for toml compat (unused)

    # ------------------------------------------------------------------
    # Event extraction parameters
    # ------------------------------------------------------------------
    half_life_base: float = 14.0        # Ebbinghaus base half-life in days (strength=1.0)
    strength_cap: float = 3.0           # upper bound on memory strength (slows decay)

    # ------------------------------------------------------------------
    # Retrieval tuning (advanced)
    # ------------------------------------------------------------------
    mmr_lambda: float = 0.7             # MMR trade-off: 1.0 = pure relevance, 0.0 = pure diversity
    recall_stochastic: bool = True       # if True, use score-as-probability sampling instead of top-k
    graph_edge_decay_half_life: float = 30.0  # days — edge weight decays toward 0 over time
    graph_spread_steps: int = 2               # propagation hops for spreading activation
    graph_spread_decay: float = 0.5           # energy multiplier per hop
    graph_inhibition_factor: float = 0.3      # lateral inhibition when multiple sources co-fire
    # Edge-type multipliers: how much energy each relation type propagates.
    # Keys: causal | cause | remind | elaboration | semantic | temporal | contrast
    graph_edge_type_weights: dict = field(default_factory=lambda: {
        "causal":      1.4,
        "cause":       1.4,
        "remind":      1.2,
        "elaboration": 1.0,
        "semantic":    0.8,
        "temporal":    0.5,
        "contrast":    0.3,
    })

    # ------------------------------------------------------------------
    # Conductor / Gorge parameters
    # ------------------------------------------------------------------
    gorge_max_beat: int = 30                  # safety valve: force cut if buffer exceeds this
    gorge_min_depth: float = 0.01             # depth floor to filter noise peaks
    gorge_stream_confirm: int = 2             # beats after candidate before confirming cut
    drift_threshold: float = 0.65             # cosine below this triggers recall refresh
    recall_top_k: int = 3                     # max events returned by recall retrieval
    memory_mode: str = "manual"               # "manual" = flow+vectors only; "auto" = gorge+recall+edges

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
    graph_edge_provider: str = ""      # "deepseek" | "openai" — REQUIRED for event naming
    graph_edge_model: str = "deepseek-chat"
    graph_edge_base_url: str = "https://api.deepseek.com"
    graph_edge_api_key_env: str = "FIAM_GRAPH_API_KEY"

    # ------------------------------------------------------------------
    # Multimodal routing
    # ------------------------------------------------------------------
    vision_provider: str = "openai_compatible"
    vision_model: str = "gpt-4o-mini"
    vision_base_url: str = ""
    vision_api_key_env: str = "FIAM_VISION_API_KEY"
    stt_provider: str = "openai_compatible"
    stt_model: str = "whisper-1"
    stt_base_url: str = ""
    stt_api_key_env: str = "FIAM_STT_API_KEY"
    tts_provider: str = "openai_compatible"
    tts_model: str = "gpt-4o-mini-tts"
    tts_base_url: str = ""
    tts_api_key_env: str = "FIAM_TTS_API_KEY"

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------
    debug_mode: bool = False

    # ------------------------------------------------------------------
    # Daemon
    # ------------------------------------------------------------------
    idle_timeout_minutes: int = 30      # minutes of no JSONL activity → trigger processing
    poll_interval_seconds: int = 30     # how often to check JSONL mtime
    email_poll_interval: int = 600      # seconds between email inbox polls (used by bridge_email)

    # ------------------------------------------------------------------
    # MQTT bus (Mosquitto)
    # ------------------------------------------------------------------
    mqtt_host: str = "127.0.0.1"        # broker host (loopback by default — internal only)
    mqtt_port: int = 1883               # broker port
    mqtt_keepalive: int = 60            # seconds

    # ------------------------------------------------------------------
    # Communication (outbox dispatch)
    # ------------------------------------------------------------------
    email_from: str = ""                           # AI's address (e.g. ai@example.com)
    email_to: str = ""                             # User's address
    email_smtp_host: str = ""                      # SMTP host (e.g. 127.0.0.1 for ProtonMail Bridge)
    email_smtp_port: int = 1025

    # ------------------------------------------------------------------
    # Budget / Quota
    # ------------------------------------------------------------------
    daily_budget_usd: float = 0.0     # 0 = unlimited; positive = daily spend cap

    # ------------------------------------------------------------------
    # Claude Code parameters (daemon wake invocation)
    # ------------------------------------------------------------------
    cc_model: str = ""                # e.g. "opus", "sonnet", "claude-opus-4-6"; empty = CC default
    cc_disallowed_tools: str = ""     # comma-separated tool names to disable (e.g. "WebFetch,NotebookEdit")

    # ------------------------------------------------------------------
    # API runtime parameters (OpenAI-compatible chat completions)
    # ------------------------------------------------------------------
    api_provider: str = "openai_compatible"
    api_model: str = "anthropic/claude-3.5-haiku"
    api_base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    api_temperature: float = 0.7
    api_max_tokens: int = 2048
    api_timeout_seconds: int = 60
    api_tools_enabled: bool = True     # enable text-editor + read tools (function calling)
    api_tools_max_loops: int = 10      # safety cap on tool-call iterations

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


    # ------------------------------------------------------------------
    # Derived paths — code side (store/ = the "basement")
    # ------------------------------------------------------------------

    @property
    def store_dir(self) -> Path:
        return self.code_path / "store"

    # --- Pool (v2 unified storage) ---

    @property
    def pool_dir(self) -> Path:
        return self.store_dir / "pool"

    # --- Flow (narrative stream) ---

    @property
    def flow_path(self) -> Path:
        """flow.jsonl — append-only beat stream."""
        return self.store_dir / "flow.jsonl"

    @property
    def feature_dir(self) -> Path:
        """Frozen beat-level embedding features for annotation/training."""
        return self.store_dir / "features"

    @property
    def annotation_state_path(self) -> Path:
        """Manual annotation progress; processed flow ranges are locked."""
        return self.store_dir / "annotation_state.json"

    @property
    def logs_dir(self) -> Path:
        return self.code_path / "logs"

    @property
    def plugins_dir(self) -> Path:
        """Functional integration manifests, one directory per optional plugin."""
        return self.code_path / "plugins"

    # ------------------------------------------------------------------
    # Derived paths — home side (AI's home, human-readable only)
    # ------------------------------------------------------------------

    @property
    def background_path(self) -> Path:
        return self.home_path / "recall.md"

    @property
    def claude_md_path(self) -> Path:
        """Legacy alias. Prefer constitution_md_path. Kept for backward compat."""
        return self.home_path / "CLAUDE.md"

    @property
    def constitution_md_path(self) -> Path:
        """Project knowledge / environment / system guide injected as system[0].

        Renamed from CLAUDE.md so CC no longer auto-loads it; fiam owns it.
        """
        return self.home_path / "constitution.md"

    @property
    def self_dir(self) -> Path:
        """AI's private space — personality, journal, inner thoughts."""
        return self.home_path / "self"

    @property
    def personality_path(self) -> Path:
        return self.self_dir / "personality.md"

    @property
    def state_path(self) -> Path:
        """Current psychological state snapshot (written by appraisal)."""
        return self.self_dir / "state.md"

    @property
    def goals_path(self) -> Path:
        """AI's self-maintained goals and desires."""
        return self.self_dir / "goals.md"

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
    def pending_external_path(self) -> Path:
        """Pre-formatted external messages waiting for CC delivery (Conductor-prepared)."""
        return self.home_path / "pending_external.txt"

    @property
    def active_session_path(self) -> Path:
        """Tracks the current CC session ID for resume-based messaging."""
        return self.self_dir / "active_session.json"

    @property
    def sleep_state_path(self) -> Path:
        """Deprecated legacy sleep state path; use ai_state_path."""
        return self.self_dir / "sleep_state.json"

    @property
    def ai_state_path(self) -> Path:
        """Current mutually exclusive AI state.

        state is one of notify/mute/block/sleep/busy/together/online.
        sleep state uses until = ISO datetime or "open".
        """
        return self.self_dir / "ai_state.json"

    @property
    def daily_summary_path(self) -> Path:
        """Daily operational summary injected by SessionStart hook."""
        return self.self_dir / "daily_summary.md"

    @property
    def interactive_lock_path(self) -> Path:
        """Lock file written by SessionStart hook when human is at terminal."""
        return self.home_path / "interactive.lock"

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
            self.logs_dir,
            self.plugins_dir,
            # Pool (v2)
            self.pool_dir / "events",
            self.feature_dir,
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
        paths_list = ", ".join(f'"{p.as_posix()}"' for p in self.home_paths)
        lines = [
            f'home_path = "{self.home_path.as_posix()}"',
            f'home_paths = [{paths_list}]',
            f'ai_name = "{self.ai_name}"',
            f'user_name = "{self.user_name}"',
            f'language_profile = "{self.language_profile}"',
            "",
            "[models]",
            f'embedding = "{self.embedding_model}"',
            f"embedding_dim = {self.embedding_dim}",
            f'embedding_backend = "{self.embedding_backend}"',
            f'embedding_remote_url = "{self.embedding_remote_url}"',
            "",
            "[retrieval]",
            f"top_k = {self.top_k}",
            f"min_score = {self.min_score}",
            f"semantic_weight = {self.semantic_weight}",
            f"recency_weight = {self.recency_weight}",
            f"temporal_link_weight = {self.temporal_link_weight}",
            f"diversity_threshold = {self.diversity_threshold}",
            f"min_event_age_hours = {self.min_event_age_hours}",
            f"temporal_window_hours = {self.temporal_window_hours}",
            "",
            "[extraction]",
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
            f"email_poll_interval = {self.email_poll_interval}",
            "",
            "[api]",
            f'provider = "{self.api_provider}"',
            f'model = "{self.api_model}"',
            f'base_url = "{self.api_base_url}"',
            f'api_key_env = "{self.api_key_env}"',
            f"temperature = {self.api_temperature}",
            f"max_tokens = {self.api_max_tokens}",
            f"timeout_seconds = {self.api_timeout_seconds}",
            f"tools_enabled = {str(self.api_tools_enabled).lower()}",
            f"tools_max_loops = {self.api_tools_max_loops}",
            "",
            "[conductor]",
            f'memory_mode = "{self.memory_mode}"',
            f"gorge_max_beat = {self.gorge_max_beat}",
            f"gorge_min_depth = {self.gorge_min_depth}",
            f"gorge_stream_confirm = {self.gorge_stream_confirm}",
            f"drift_threshold = {self.drift_threshold}",
            f"recall_top_k = {self.recall_top_k}",
            "",
            "[mqtt]",
            f'host = "{self.mqtt_host}"',
            f"port = {self.mqtt_port}",
            f"keepalive = {self.mqtt_keepalive}",
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
            "[vision]",
            f'provider = "{self.vision_provider}"',
            f'model = "{self.vision_model}"',
            f'base_url = "{self.vision_base_url}"',
            f'api_key_env = "{self.vision_api_key_env}"',
            "",
            "[voice.stt]",
            f'provider = "{self.stt_provider}"',
            f'model = "{self.stt_model}"',
            f'base_url = "{self.stt_base_url}"',
            f'api_key_env = "{self.stt_api_key_env}"',
            "",
            "[voice.tts]",
            f'provider = "{self.tts_provider}"',
            f'model = "{self.tts_model}"',
            f'base_url = "{self.tts_base_url}"',
            f'api_key_env = "{self.tts_api_key_env}"',
            "",
            "[comms]",
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
        api = raw.get("api", {})
        features = raw.get("features", {})
        comm = raw.get("communication", raw.get("comms", {}))
        graph = raw.get("graph", {})
        conductor_cfg = raw.get("conductor", {})
        mqtt_cfg = raw.get("mqtt", {})
        vision = raw.get("vision", {})
        voice = raw.get("voice", {})
        stt = voice.get("stt", {}) if isinstance(voice, dict) else {}
        tts = voice.get("tts", {}) if isinstance(voice, dict) else {}

        return cls(
            home_path=home_path,
            home_paths=home_paths,
            code_path=code_path,
            ai_name=raw.get("ai_name", ""),
            user_name=raw.get("user_name", ""),
            language_profile=raw.get("language_profile", "multi"),
            # Models
            embedding_model=models.get("embedding",
                                       # backward compat: old toml had embedding_zh/embedding_en
                                       models.get("embedding_zh", "")),
            embedding_dim=models.get("embedding_dim", 0),  # 0 = derive from profile
            embedding_backend=models.get("embedding_backend", "local"),
            embedding_remote_url=models.get("embedding_remote_url", ""),
            # Retrieval
            top_k=retrieval.get("top_k", cls.top_k),
            min_score=retrieval.get("min_score", cls.min_score),
            semantic_weight=retrieval.get("semantic_weight", cls.semantic_weight),
            recency_weight=retrieval.get("recency_weight", cls.recency_weight),
            temporal_link_weight=retrieval.get("temporal_link_weight", cls.temporal_link_weight),
            diversity_threshold=retrieval.get("diversity_threshold", cls.diversity_threshold),
            min_event_age_hours=retrieval.get("min_event_age_hours", cls.min_event_age_hours),
            temporal_window_hours=retrieval.get("temporal_window_hours", cls.temporal_window_hours),
            # Extraction
            half_life_base=extraction.get("half_life_base", cls.half_life_base),
            strength_cap=extraction.get("strength_cap", 3.0),
            # Retrieval tuning (advanced)
            mmr_lambda=retrieval.get("mmr_lambda", 0.7),
            recall_stochastic=retrieval.get("stochastic", True),
            graph_edge_decay_half_life=graph.get("edge_decay_half_life_days", 30.0),
            graph_spread_steps=graph.get("spread_steps", 2),
            graph_spread_decay=graph.get("spread_decay_per_step", 0.5),
            graph_inhibition_factor=graph.get("inhibition_factor", 0.3),
            graph_edge_type_weights=dict(graph.get("edge_type_weights", {
                "causal": 1.4, "cause": 1.4, "remind": 1.2, "elaboration": 1.0,
                "semantic": 0.8, "temporal": 0.5, "contrast": 0.3,
            })),
            # Conductor
            gorge_max_beat=conductor_cfg.get("gorge_max_beat", cls.gorge_max_beat),
            gorge_min_depth=conductor_cfg.get("gorge_min_depth", cls.gorge_min_depth),
            gorge_stream_confirm=conductor_cfg.get("gorge_stream_confirm", cls.gorge_stream_confirm),
            drift_threshold=conductor_cfg.get("drift_threshold", cls.drift_threshold),
            recall_top_k=conductor_cfg.get("recall_top_k", cls.recall_top_k),
            memory_mode=conductor_cfg.get("memory_mode", cls.memory_mode),
            # Narrative
            narrative_llm_enabled=narrative.get("llm_enabled", cls.narrative_llm_enabled),
            narrative_llm_provider=narrative.get("llm_provider", cls.narrative_llm_provider),
            narrative_llm_model=narrative.get("llm_model", cls.narrative_llm_model),
            narrative_llm_base_url=narrative.get("llm_base_url", cls.narrative_llm_base_url),
            narrative_llm_api_key_env=narrative.get("llm_api_key_env", cls.narrative_llm_api_key_env),
            # Daemon
            email_poll_interval=daemon.get("email_poll_interval", cls.email_poll_interval),
            idle_timeout_minutes=daemon.get("idle_timeout_minutes", cls.idle_timeout_minutes),
            poll_interval_seconds=daemon.get("poll_interval_seconds", cls.poll_interval_seconds),
            daily_budget_usd=daemon.get("daily_budget_usd", cls.daily_budget_usd),
            cc_model=daemon.get("cc_model", cls.cc_model),
            cc_disallowed_tools=daemon.get("cc_disallowed_tools", cls.cc_disallowed_tools),
            # API runtime
            api_provider=api.get("provider", cls.api_provider),
            api_model=api.get("model", cls.api_model),
            api_base_url=api.get("base_url", cls.api_base_url),
            api_key_env=api.get("api_key_env", cls.api_key_env),
            api_temperature=api.get("temperature", cls.api_temperature),
            api_max_tokens=api.get("max_tokens", cls.api_max_tokens),
            api_timeout_seconds=api.get("timeout_seconds", cls.api_timeout_seconds),
            api_tools_enabled=api.get("tools_enabled", cls.api_tools_enabled),
            api_tools_max_loops=api.get("tools_max_loops", cls.api_tools_max_loops),
            # MQTT
            mqtt_host=mqtt_cfg.get("host", cls.mqtt_host),
            mqtt_port=mqtt_cfg.get("port", cls.mqtt_port),
            mqtt_keepalive=mqtt_cfg.get("keepalive", cls.mqtt_keepalive),
            # Features
            git_enabled=features.get("git_enabled", cls.git_enabled),
            # Graph edge typing
            graph_edge_provider=graph.get("edge_provider", cls.graph_edge_provider),
            graph_edge_model=graph.get("edge_model", cls.graph_edge_model),
            graph_edge_base_url=graph.get("edge_base_url", cls.graph_edge_base_url),
            graph_edge_api_key_env=graph.get("edge_api_key_env", cls.graph_edge_api_key_env),
            # Multimodal routing
            vision_provider=vision.get("provider", cls.vision_provider),
            vision_model=vision.get("model", cls.vision_model),
            vision_base_url=vision.get("base_url", cls.vision_base_url),
            vision_api_key_env=vision.get("api_key_env", cls.vision_api_key_env),
            stt_provider=stt.get("provider", cls.stt_provider),
            stt_model=stt.get("model", cls.stt_model),
            stt_base_url=stt.get("base_url", cls.stt_base_url),
            stt_api_key_env=stt.get("api_key_env", cls.stt_api_key_env),
            tts_provider=tts.get("provider", cls.tts_provider),
            tts_model=tts.get("model", cls.tts_model),
            tts_base_url=tts.get("base_url", cls.tts_base_url),
            tts_api_key_env=tts.get("api_key_env", cls.tts_api_key_env),
            # Communication
            email_from=comm.get("email_from", ""),
            email_to=comm.get("email_to", ""),
            email_smtp_host=comm.get("email_smtp_host", ""),
            email_smtp_port=comm.get("email_smtp_port", cls.email_smtp_port),
        )
