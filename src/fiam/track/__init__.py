"""Studio 记录官 (track) — git-log / traces → 分层 markdown summary in vault `track/`.

See STUDIO_ROADMAP.md (Phase 1-2) and STUDIO_CONVENTIONS.md (§6).
"""

from .config import TrackConfig, load_track_config
from .recall import recall

__all__ = ["TrackConfig", "load_track_config", "recall"]
