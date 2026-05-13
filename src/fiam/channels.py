"""Declarative channel registry for runtime routing decisions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    channel_id: str
    actor: str = "user"
    responds: bool = True


CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec("chat", actor="user", responds=True),
    ChannelSpec("studio", actor="user", responds=True),
    ChannelSpec("stroll", actor="user", responds=True),
    ChannelSpec("browser", actor="user", responds=True),
    ChannelSpec("email", actor="external", responds=True),
    ChannelSpec("schedule", actor="system", responds=True),
    ChannelSpec("limen", actor="system", responds=False),
    ChannelSpec("ring", actor="system", responds=False),
)


_BY_ID = {item.channel_id: item for item in CHANNELS}


def normalize_channel(channel: str) -> str:
    value = (channel or "").strip().lower()
    return value or "chat"


def channel_spec(channel: str) -> ChannelSpec:
    canon = normalize_channel(channel)
    return _BY_ID.get(canon, ChannelSpec(canon))


def actor_for_channel(channel: str) -> str:
    return channel_spec(channel).actor


def channel_responds(channel: str) -> bool:
    return channel_spec(channel).responds


def channel_ids() -> set[str]:
    return set(_BY_ID)
