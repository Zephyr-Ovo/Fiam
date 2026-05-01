"""OpenAI-compatible API runtime for fiam."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

import numpy as np

from fiam.runtime.prompt import build_api_messages
from fiam.runtime.turns import assistant_text_beats, user_beat


class ApiClient(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> "ApiCompletion":
        ...


@dataclass(frozen=True, slots=True)
class ApiCompletion:
    text: str
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApiRuntimeResult:
    ok: bool
    backend: str
    reply: str
    model: str
    usage: dict[str, Any]
    recall_fragments: int = 0
    dispatched: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class OpenAICompatibleClient:
    """Tiny OpenAI-compatible chat completions client."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: int = 60,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    @classmethod
    def from_config(cls, config) -> "OpenAICompatibleClient":
        api_key_env = config.api_key_env or "FIAM_API_KEY"
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Missing env var: {api_key_env}")
        return cls(
            base_url=config.api_base_url,
            api_key=api_key,
            timeout=config.api_timeout_seconds,
            extra_headers={"X-Title": "fiam"},
        )

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> ApiCompletion:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            **self.extra_headers,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"API request failed ({exc.code}): {detail}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("API response has no choices")
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "").strip()
        if not text:
            raise RuntimeError("API response has empty message content")
        return ApiCompletion(
            text=text,
            model=str(data.get("model") or model),
            usage=dict(data.get("usage") or {}),
            raw=data,
        )


class ApiRuntime:
    """Fiam-compatible runtime backed by an OpenAI-compatible API."""

    def __init__(
        self,
        config,
        *,
        client: ApiClient | None = None,
        conductor=None,
        dispatcher: Callable[[str, str, str], bool] | None = None,
        recall_refresher: Callable[[np.ndarray], int] | None = None,
    ) -> None:
        self.config = config
        self.client = client or OpenAICompatibleClient.from_config(config)
        self.conductor = conductor
        self.dispatcher = dispatcher
        self.recall_refresher = recall_refresher

    @classmethod
    def from_config(cls, config, **kwargs) -> "ApiRuntime":
        return cls(config, **kwargs)

    def ask(
        self,
        text: str,
        *,
        source: str = "api",
        record: bool = True,
        include_recall: bool = True,
    ) -> ApiRuntimeResult:
        clean = text.strip()
        if not clean:
            raise ValueError("missing text")

        recall_fragments = 0
        if record:
            recall_fragments = self._record_user(clean, source=source)

        messages = build_api_messages(
            self.config,
            clean,
            source=source,
            include_recall=include_recall,
            consume_recall_dirty=True,
        )
        completion = self.client.complete(
            messages=messages,
            model=self.config.api_model,
            temperature=self.config.api_temperature,
            max_tokens=self.config.api_max_tokens,
        )

        dispatched = self._dispatch(completion.text)
        if record:
            self._record_assistant(completion.text, source="api")

        return ApiRuntimeResult(
            ok=True,
            backend="api",
            reply=completion.text,
            model=completion.model,
            usage=completion.usage,
            recall_fragments=recall_fragments,
            dispatched=dispatched,
            raw=completion.raw,
        )

    def _record_user(self, text: str, *, source: str) -> int:
        if self.conductor is None:
            return 0
        meta = {"runtime": "api", "input_source": source, "role": "user"}
        beat = user_beat(
            text,
            t=datetime.now(timezone.utc),
            source="api",
            user_status=self.conductor.user_status,
            ai_status=self.conductor.ai_status,
            user_name=getattr(self.config, "user_name", "") or "zephyr",
            meta=meta,
        )
        self.conductor._ingest_beat(beat)
        vec = getattr(self.conductor, "last_ingested_vector", None)
        if self.recall_refresher is not None and vec is not None:
            return self.recall_refresher(vec)
        return 0

    def _record_assistant(self, text: str, *, source: str) -> None:
        if self.conductor is None:
            return
        meta = {"runtime": "api", "role": "assistant"}
        for beat in assistant_text_beats(
            text,
            t=datetime.now(timezone.utc),
            source=source,
            user_status=self.conductor.user_status,
            ai_status=self.conductor.ai_status,
            ai_name=getattr(self.config, "ai_name", "") or "ai",
            meta=meta,
        ):
            self.conductor._ingest_beat(beat)

    def _dispatch(self, text: str) -> int:
        if self.dispatcher is None:
            return 0
        from fiam.markers import parse_outbound_markers

        count = 0
        for marker in parse_outbound_markers(text):
            if self.dispatcher(marker.channel, marker.recipient, marker.body):
                count += 1
        return count