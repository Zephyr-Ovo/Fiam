"""
Conversation dynamics extraction from event body text.

Parses [user]/[assistant] blocks and extracts natural-language
descriptions of conversational rhythm and asymmetry.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta


def parse_body_blocks(body: str) -> list[dict[str, str]]:
    """Split event body into role/content blocks.

    Body format:
        [user]
        message text

        [assistant]
        response text
        ...

    Returns list of {"role": "user"|"assistant", "content": "..."}.
    """
    pattern = re.compile(r"\[(user|assistant)\]\s*\n", re.IGNORECASE)
    splits = pattern.split(body)
    # splits looks like: ['', 'user', 'text...', 'assistant', 'text...', ...]

    blocks: list[dict[str, str]] = []
    i = 1  # skip leading empty string
    while i + 1 < len(splits):
        role = splits[i].lower()
        content = splits[i + 1].strip()
        if content:
            blocks.append({"role": role, "content": content})
        i += 2

    return blocks


def extract_dynamics(body: str) -> str:
    """Extract natural-language description of conversation dynamics.

    Detects:
    - Length asymmetry (user vs assistant message lengths)
    - Message count / exchange density
    - One-sided patterns

    Returns a short Chinese fragment (e.g. "他的话明显更长") or empty string.
    """
    blocks = parse_body_blocks(body)
    if len(blocks) < 2:
        return ""

    user_lengths = [len(b["content"]) for b in blocks if b["role"] == "user"]
    assistant_lengths = [len(b["content"]) for b in blocks if b["role"] == "assistant"]

    if not user_lengths or not assistant_lengths:
        return ""

    user_avg = sum(user_lengths) / len(user_lengths)
    assistant_avg = sum(assistant_lengths) / len(assistant_lengths)

    parts: list[str] = []

    # Exchange density
    pair_count = min(len(user_lengths), len(assistant_lengths))
    if pair_count >= 6:
        parts.append("聊了很久")
    elif pair_count >= 3:
        parts.append("说了好几轮")

    # Length asymmetry
    asymmetry = abs(user_avg - assistant_avg)
    if asymmetry > 100:
        if user_avg > assistant_avg:
            parts.append("他的话明显更长")
        else:
            parts.append("我说得更多")

    return "，".join(parts) if parts else ""


def relative_time(timestamp: datetime) -> str:
    """Convert a datetime to a natural relative-time expression."""
    now = datetime.now(timestamp.tzinfo)
    delta = now - timestamp

    if delta < timedelta(hours=12):
        return "今天"
    elif delta < timedelta(days=1):
        return "昨天"
    elif delta < timedelta(days=7):
        return "前几天"
    else:
        return timestamp.strftime("%m.%d")
