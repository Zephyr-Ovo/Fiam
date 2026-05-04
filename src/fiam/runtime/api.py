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
from fiam.runtime.tools import TOOL_SCHEMAS, execute_tool_call
from fiam.runtime.turns import assistant_text_beats, user_beat
from fiam.store.beat import Beat


class ApiClient(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> "ApiCompletion":
        ...


@dataclass(frozen=True, slots=True)
class ApiCompletion:
    text: str
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""


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
    tool_loops: int = 0


def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
        elif isinstance(value, dict):
            nested = total.setdefault(key, {})
            if isinstance(nested, dict):
                _merge_usage(nested, value)


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
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> ApiCompletion:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
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
        choice = choices[0]
        message = choice.get("message") or {}
        text = str(message.get("content") or "").strip()
        tool_calls = list(message.get("tool_calls") or [])
        if not text and not tool_calls:
            raise RuntimeError("API response has neither content nor tool_calls")
        return ApiCompletion(
            text=text,
            model=str(data.get("model") or model),
            usage=dict(data.get("usage") or {}),
            raw=data,
            tool_calls=tool_calls,
            finish_reason=str(choice.get("finish_reason") or ""),
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

        tools_enabled = bool(getattr(self.config, "api_tools_enabled", False))
        tools = TOOL_SCHEMAS if tools_enabled else None
        max_loops = max(1, int(getattr(self.config, "api_tools_max_loops", 10)))

        loops = 0
        usage_total: dict[str, Any] = {}
        completion: ApiCompletion | None = None
        while True:
            loops += 1
            completion = self.client.complete(
                messages=messages,
                model=self.config.api_model,
                temperature=self.config.api_temperature,
                max_tokens=self.config.api_max_tokens,
                tools=tools,
            )
            _merge_usage(usage_total, completion.usage)
            if not completion.tool_calls:
                break
            # Append the assistant message verbatim so the next request preserves
            # the tool_call_ids the model issued.
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": completion.text or None,
                "tool_calls": completion.tool_calls,
            }
            messages.append(assistant_msg)
            for call in completion.tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                raw_args = str(fn.get("arguments") or "{}")
                result = execute_tool_call(self.config, name, raw_args)
                if record:
                    self._record_tool_action(name, raw_args, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "name": name,
                    "content": result,
                })
            if loops >= max_loops:
                # Force one final no-tools call so the model emits a user-facing reply.
                completion = self.client.complete(
                    messages=messages,
                    model=self.config.api_model,
                    temperature=self.config.api_temperature,
                    max_tokens=self.config.api_max_tokens,
                    tools=None,
                )
                _merge_usage(usage_total, completion.usage)
                break

        assert completion is not None
        reply_text = completion.text or "(empty reply)"
        dispatched = self._dispatch(reply_text)
        if record:
            self._record_assistant(reply_text, source="api")

        return ApiRuntimeResult(
            ok=True,
            backend="api",
            reply=reply_text,
            model=completion.model,
            usage=usage_total or completion.usage,
            recall_fragments=recall_fragments,
            dispatched=dispatched,
            raw=completion.raw,
            tool_loops=loops,
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

    def _record_tool_action(self, name: str, raw_args: str, result: str) -> None:
        if self.conductor is None:
            return
        summary = result.replace("\n", " ")[:300]
        text = f"api tool {name}({raw_args[:300]}) -> {summary}"
        self.conductor._ingest_beat(Beat(
            t=datetime.now(timezone.utc),
            text=text,
            source="action",
            user=self.conductor.user_status,
            ai=self.conductor.ai_status,
            meta={"runtime": "api", "tool": name},
        ))

    def _dispatch(self, text: str) -> int:
        if self.dispatcher is None:
            return 0
        from fiam.markers import parse_outbound_markers

        count = 0
        for marker in parse_outbound_markers(text):
            if self.dispatcher(marker.channel, marker.recipient, marker.body):
                count += 1
        return count