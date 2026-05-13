"""Shared recall context builder for runtime backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np

from fiam.retriever.spread import retrieve

if TYPE_CHECKING:
    from fiam.config import FiamConfig
    from fiam.store.pool import Pool


@dataclass(frozen=True)
class RecallFragment:
    event_id: str
    time_hint: str
    activation: float
    summary: str
    reason: str = "spreading_activation"
    channel: str = ""
    surface: str = ""
    kind: str = "event"
    object_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RecallContext:
    fragments: tuple[RecallFragment, ...] = field(default_factory=tuple)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "pool.spreading_activation"
    shield_after: datetime | None = None

    @property
    def count(self) -> int:
        return len(self.fragments)

    def render(self, *, max_chars: int = 4000) -> str:
        if not self.fragments:
            return ""
        lines: list[str] = []
        for fragment in self.fragments:
            meta = [fragment.time_hint, f"activation={fragment.activation:.2f}"]
            if fragment.channel:
                meta.append(f"channel={fragment.channel}")
            if fragment.surface:
                meta.append(f"surface={fragment.surface}")
            if fragment.kind and fragment.kind != "event":
                meta.append(f"kind={fragment.kind}")
            line = f"- [{fragment.event_id}] ({'; '.join(meta)}) {fragment.summary}"
            if fragment.object_refs:
                line += " refs=" + ",".join(fragment.object_refs)
            lines.append(line)
        text = "\n".join(lines).strip()
        if max_chars > 0 and len(text) > max_chars:
            return text[: max_chars - 3].rstrip() + "..."
        return text


def empty_recall_context() -> RecallContext:
    return RecallContext(fragments=())


def build_recall_context(
    config: "FiamConfig",
    pool: "Pool",
    query_vec: np.ndarray,
    *,
    top_k: int | None = None,
    shield_recent: bool = True,
    shield_after: datetime | None = None,
) -> RecallContext:
    """Build bounded per-turn recall context from the Pool graph.

    When ``shield_recent`` is True (default), suppress events created today so
    automatic recall does not surface in-flight context. Pass False for manual
    recall flows that explicitly want recent events included.

    ``shield_after`` overrides the default today-midnight cutoff: any event
    whose ``t >= shield_after`` is suppressed. Used by the chat /recall
    endpoint to exclude events from the *current* session window (events
    created since the last session boundary are still in the AI's live
    context, so re-surfacing them via recall would be redundant).
    """
    if shield_after is None:
        shield_after = (
            datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            if shield_recent
            else None
        )
    results = retrieve(
        query_vec,
        pool,
        shield_after=shield_after,
        top_k=top_k or config.recall_top_k,
    )
    if not results:
        return RecallContext(shield_after=shield_after)

    now = datetime.now(timezone.utc)
    fragments: list[RecallFragment] = []

    for event_id, activation in results:
        ev = pool.get_event(event_id)
        if ev is None:
            continue
        body = pool.read_body(event_id)
        fragment = body.strip()[:400]
        if len(body.strip()) > 400:
            fragment += "..."

        age = now - ev.t
        if age.days > 30:
            hint = f"{age.days // 30}个月前"
        elif age.days > 0:
            hint = f"{age.days}天前"
        elif age.seconds > 3600:
            hint = f"{age.seconds // 3600}小时前"
        else:
            hint = "刚才"

        privacy = str(getattr(ev, "privacy", "") or "public").lower()
        kind = str(getattr(ev, "kind", "") or "event").lower()
        if privacy in {"private", "thought"} or kind in {"trace", "control", "dispatch_raw", "hold"}:
            continue
        object_refs_raw = getattr(ev, "object_refs", ()) or ()
        object_refs = tuple(str(item) for item in object_refs_raw if str(item).strip())
        fragments.append(RecallFragment(
            event_id=event_id,
            time_hint=hint,
            activation=float(activation),
            summary=fragment,
            channel=str(getattr(ev, "channel", "") or ""),
            surface=str(getattr(ev, "surface", "") or ""),
            kind=kind,
            object_refs=object_refs,
        ))
        ev.access_count += 1

    if not fragments:
        return RecallContext(shield_after=shield_after)

    pool.save_events()
    return RecallContext(
        fragments=tuple(fragments),
        generated_at=now,
        shield_after=shield_after,
    )