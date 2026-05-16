"""OpenAI-compatible API runtime for fiam."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from fiam.runtime.prompt import PromptAssembler, _valid_transcript_message
from fiam.runtime.tools import TOOL_SCHEMAS, execute_tool_call


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
    reasoning: str = ""


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
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    transcript_messages: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=dict)
    thinking_blocks: list[dict[str, Any]] = field(default_factory=list)


def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
        elif isinstance(value, dict):
            nested = total.setdefault(key, {})
            if isinstance(nested, dict):
                _merge_usage(nested, value)


def _image_attachment_blocks(config, attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not attachments:
        return []
    blocks: list[dict[str, Any]] = []
    from fiam.store.objects import ObjectStore

    object_store = ObjectStore(config.object_dir)
    for att in attachments:
        if not isinstance(att, dict):
            continue
        mime = str(att.get("mime") or "").strip().lower()
        if not mime.startswith("image/"):
            continue
        object_hash = "".join(ch for ch in str(att.get("object_hash") or "").lower() if ch in "0123456789abcdef")
        if len(object_hash) != 64:
            continue
        data = object_store.get_bytes(object_hash, suffix="")
        if not data:
            continue
        if len(data) > 8 * 1024 * 1024:
            continue
        encoded = base64.b64encode(data).decode("ascii")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{encoded}"},
        })
    return blocks


def _summarize_tool_input(name: str, raw_args: str) -> str:
    try:
        args = json.loads(raw_args)
    except (json.JSONDecodeError, TypeError):
        return raw_args[:200]
    if name == "Bash":
        return str(args.get("command") or "")[:200]
    if name == "Read":
        return str(args.get("path") or args.get("file_path") or "")[:200]
    if name in ("Edit", "Write"):
        return str(args.get("path") or args.get("file_path") or "")[:200]
    parts = []
    for k, v in (args if isinstance(args, dict) else {}).items():
        parts.append(f"{k}={str(v)[:60]}")
    return ", ".join(parts)[:200]


def _bounded_tool_result(config, text: str, *, limit: int = 4000) -> tuple[str, str, int]:
    raw = str(text or "")
    size = len(raw.encode("utf-8"))
    if size <= limit:
        return raw, "", size
    from fiam.store.objects import ObjectStore

    object_hash = ObjectStore(config.object_dir).put_text(raw, suffix=".txt")
    preview = raw[:limit].rstrip()
    bounded = (
        f"{preview}\n\n"
        f"[object_ref hash={object_hash} size={size} mime=text/plain reason=tool_result_truncated]"
    )
    return bounded, object_hash, size


def _attach_image_blocks_to_last_user_message(messages: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> bool:
    if not blocks or not messages:
        return False
    user_message = messages[-1]
    if user_message.get("role") != "user":
        return False
    content = user_message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(str(block.get("text") or "") for block in content if isinstance(block, dict)).strip()
    else:
        text = ""
    user_message["content"] = [{"type": "text", "text": text}, *blocks]
    return True


def _attach_images_to_last_user_message(config, messages: list[dict[str, Any]], attachments: list[dict[str, Any]] | None) -> bool:
    return _attach_image_blocks_to_last_user_message(messages, _image_attachment_blocks(config, attachments))


def _append_image_description_to_last_user_message(messages: list[dict[str, Any]], description: str) -> None:
    if not messages:
        return
    user_message = messages[-1]
    if user_message.get("role") != "user":
        return
    addition = f"\n\n[image description fallback]\n{description.strip()}"
    content = user_message.get("content")
    if isinstance(content, str):
        user_message["content"] = content + addition
    elif isinstance(content, list):
        user_message["content"] = [*content, {"type": "text", "text": addition.strip()}]
    else:
        user_message["content"] = addition.strip()


def _model_supports_images(model: str) -> bool:
    name = (model or "").lower()
    markers = (
        "gpt-4o",
        "gpt-4.1",
        "gpt-4.5",
        "gemini",
        "claude-3",
        "qwen-vl",
        "qwen2-vl",
        "qwen2.5-vl",
        "pixtral",
        "vision",
        "vl-",
        "llava",
    )
    return any(marker in name for marker in markers)


def _vision_api_config(config):
    return replace(
        config,
        api_provider=getattr(config, "vision_provider", "openai_compatible"),
        api_model=getattr(config, "vision_model", ""),
        api_base_url=getattr(config, "vision_base_url", "") or getattr(config, "api_base_url", ""),
        api_key_env=getattr(config, "vision_api_key_env", "") or getattr(config, "api_key_env", ""),
        api_fallback_provider="",
        api_fallback_model="",
        api_fallback_base_url="",
        api_fallback_key_env="",
    )


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
        reasoning_effort: str = "",
        thinking_budget_tokens: int = 0,
        _retries: int = 2,
    ) -> ApiCompletion:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        # Request reasoning when configured. Different providers honour different
        # fields; send the common ones so at least one lands. Anthropic-via-Poe
        # historically picks up `thinking`; OpenRouter / OpenAI o-series picks up
        # `reasoning_effort`. Unknown fields are ignored by spec-following
        # providers.
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if thinking_budget_tokens > 0:
            body["thinking"] = {"type": "enabled", "budget_tokens": int(thinking_budget_tokens)}
            # OpenRouter passes a different shape under `reasoning`.
            body["reasoning"] = {"effort": reasoning_effort or "high", "max_tokens": int(thinking_budget_tokens)}
        headers = self._headers()
        last_exc: Exception | None = None
        for attempt in range(1 + _retries):
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
            # Extract reasoning text. Providers vary:
            #   - Anthropic via Poe / DeepSeek-R1: `reasoning_content`
            #   - OpenRouter / OpenAI o-series:    `reasoning`
            #   - Some shims:                       content list with type=thinking
            reasoning_text = ""
            for key in ("reasoning_content", "reasoning"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    reasoning_text = value.strip()
                    break
                if isinstance(value, dict):
                    inner = value.get("content") or value.get("text") or ""
                    if isinstance(inner, str) and inner.strip():
                        reasoning_text = inner.strip()
                        break
            if not reasoning_text:
                content = message.get("content")
                if isinstance(content, list):
                    parts: list[str] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") in {"thinking", "reasoning"}:
                            txt = block.get("thinking") or block.get("text") or ""
                            if isinstance(txt, str) and txt.strip():
                                parts.append(txt.strip())
                    if parts:
                        reasoning_text = "\n\n".join(parts)
            if not text and not tool_calls:
                last_exc = RuntimeError(
                    f"API response has neither content nor tool_calls (attempt {attempt + 1}/{1 + _retries})"
                )
                continue
            return ApiCompletion(
                text=text,
                model=str(data.get("model") or model),
                usage=dict(data.get("usage") or {}),
                raw=data,
                tool_calls=tool_calls,
                finish_reason=str(choice.get("finish_reason") or ""),
                reasoning=reasoning_text,
            )
        raise last_exc or RuntimeError("API response has neither content nor tool_calls")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            **self.extra_headers,
        }


class GoogleOpenAICompatibleClient(OpenAICompatibleClient):
    """Google Gemini API through the OpenAI-compatible endpoint."""

    @classmethod
    def from_config(cls, config) -> "GoogleOpenAICompatibleClient":
        key_env = config.api_key_env or "GEMINI_API_KEY"
        api_key = os.environ.get(key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Missing env var: {key_env}")
        base_url = str(config.api_base_url or "").strip() or "https://generativelanguage.googleapis.com/v1beta/openai"
        return cls(base_url=base_url, api_key=api_key, timeout=config.api_timeout_seconds)


class AnthropicMessagesClient:
    """Anthropic Messages API client with an OpenAI-like ApiClient facade."""

    @classmethod
    def from_config(cls, config) -> "AnthropicMessagesClient":
        key_env = config.api_key_env or "ANTHROPIC_API_KEY"
        api_key = os.environ.get(key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Missing env var: {key_env}")
        base_url = str(config.api_base_url or "").strip().rstrip("/") or "https://api.anthropic.com/v1"
        return cls(base_url=base_url, api_key=api_key, timeout=config.api_timeout_seconds)

    def __init__(self, *, base_url: str, api_key: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> ApiCompletion:
        system, converted = self._convert_messages(messages)
        body: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [self._convert_tool_schema(tool) for tool in tools if isinstance(tool, dict)]
        request = urllib.request.Request(
            f"{self.base_url}/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"API request failed ({exc.code}): {detail}") from exc
        content = data.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                })
        return ApiCompletion(
            text="\n".join(part for part in text_parts if part).strip(),
            model=str(data.get("model") or model),
            usage=dict(data.get("usage") or {}),
            raw=data,
            tool_calls=tool_calls,
            finish_reason=str(data.get("stop_reason") or ""),
        )

    def _convert_messages(self, messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "")
            if role == "system":
                text = self._content_to_text(message.get("content"))
                if text:
                    system_parts.append(text)
                continue
            if role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": str(message.get("tool_call_id") or ""),
                        "content": str(message.get("content") or ""),
                    }],
                })
                continue
            if role == "assistant" and message.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                text = self._content_to_text(message.get("content"))
                if text:
                    blocks.append({"type": "text", "text": text})
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                    try:
                        args = json.loads(str(fn.get("arguments") or "{}"))
                    except json.JSONDecodeError:
                        args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "input": args,
                    })
                converted.append({"role": "assistant", "content": blocks})
                continue
            if role in {"user", "assistant"}:
                converted.append({"role": role, "content": self._convert_content(message.get("content"))})
        return "\n\n".join(system_parts), converted

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text") or ""))
            return "\n".join(part for part in parts if part).strip()
        return ""

    def _convert_content(self, content: Any) -> str | list[dict[str, Any]]:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                blocks.append({"type": "text", "text": str(block.get("text") or "")})
                continue
            if block.get("type") == "image_url":
                url = str((block.get("image_url") or {}).get("url") or "")
                if not url.startswith("data:") or ";base64," not in url:
                    continue
                header, data = url.split(";base64,", 1)
                media_type = header.removeprefix("data:")
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                })
        return blocks or ""

    def _convert_tool_schema(self, tool: dict[str, Any]) -> dict[str, Any]:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        return {
            "name": str(fn.get("name") or ""),
            "description": str(fn.get("description") or ""),
            "input_schema": fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object"},
        }


class VertexOpenAICompatibleClient(OpenAICompatibleClient):
    """OpenAI-compatible Vertex AI Gemini client using service-account OAuth."""

    def __init__(
        self,
        *,
        base_url: str,
        credentials,
        timeout: int = 60,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(base_url=base_url, api_key="", timeout=timeout, extra_headers=extra_headers)
        self.credentials = credentials

    @classmethod
    def from_config(cls, config) -> "VertexOpenAICompatibleClient":
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:
            raise RuntimeError("Vertex API provider requires package: google-auth") from exc

        key_env = config.api_key_env or "GOOGLE_APPLICATION_CREDENTIALS"
        key_path = os.environ.get(key_env, "").strip()
        if not key_path:
            raise RuntimeError(f"Missing env var: {key_env}")
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        info = json.loads(Path(key_path).read_text(encoding="utf-8"))
        project = (
            os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
            or os.environ.get("VERTEX_PROJECT", "").strip()
            or str(info.get("project_id") or "").strip()
        )
        if not project:
            raise RuntimeError("Missing Google Cloud project id")
        location = (
            os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
            or os.environ.get("VERTEX_LOCATION", "").strip()
            or "us-central1"
        )
        base_url = str(config.api_base_url or "").strip().rstrip("/")
        if not base_url or "openrouter.ai" in base_url:
            base_url = (
                f"https://{location}-aiplatform.googleapis.com/v1/"
                f"projects/{project}/locations/{location}/endpoints/openapi"
            )
        return cls(base_url=base_url, credentials=credentials, timeout=config.api_timeout_seconds)

    def _headers(self) -> dict[str, str]:
        try:
            from google.auth.transport.requests import Request
        except ImportError as exc:
            raise RuntimeError("Vertex API provider requires package: google-auth") from exc
        if not self.credentials.valid:
            self.credentials.refresh(Request())
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.credentials.token}",
            **self.extra_headers,
        }


class FallbackApiClient:
    def __init__(self, primary: ApiClient, fallback: ApiClient, *, fallback_model: str) -> None:
        self.primary = primary
        self.fallback = fallback
        self.fallback_model = fallback_model

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "",
        thinking_budget_tokens: int = 0,
    ) -> ApiCompletion:
        try:
            return self.primary.complete(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                reasoning_effort=reasoning_effort,
                thinking_budget_tokens=thinking_budget_tokens,
            )
        except Exception as primary_exc:
            try:
                return self.fallback.complete(
                    messages=messages,
                    model=self.fallback_model or model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    reasoning_effort=reasoning_effort,
                    thinking_budget_tokens=thinking_budget_tokens,
                )
            except Exception as fallback_exc:
                raise RuntimeError(f"Primary API failed: {primary_exc}; fallback API failed: {fallback_exc}") from fallback_exc


def _single_api_client_from_config(config) -> ApiClient:
    provider = str(getattr(config, "api_provider", "openai_compatible") or "").lower()
    if provider in {"anthropic", "anthropic_messages"}:
        return AnthropicMessagesClient.from_config(config)
    if provider in {"google", "google_openai", "google_ai", "gemini", "gemini_openai"}:
        return GoogleOpenAICompatibleClient.from_config(config)
    if provider in {"vertex", "vertex_openai", "google_vertex", "google_vertex_openai"}:
        return VertexOpenAICompatibleClient.from_config(config)
    return OpenAICompatibleClient.from_config(config)


def _fallback_api_config(config):
    provider = str(getattr(config, "api_fallback_provider", "") or "").strip()
    if not provider:
        return None
    return replace(
        config,
        api_provider=provider,
        api_model=getattr(config, "api_fallback_model", "") or getattr(config, "api_model", ""),
        api_base_url=getattr(config, "api_fallback_base_url", ""),
        api_key_env=getattr(config, "api_fallback_key_env", "") or getattr(config, "api_key_env", ""),
    )


def _api_client_from_config(config) -> ApiClient:
    primary = _single_api_client_from_config(config)
    fallback_config = _fallback_api_config(config)
    if fallback_config is None:
        return primary
    fallback = _single_api_client_from_config(fallback_config)
    return FallbackApiClient(primary, fallback, fallback_model=fallback_config.api_model)


class ApiRuntime:
    """Fiam-compatible runtime backed by an OpenAI-compatible API."""

    def __init__(
        self,
        config,
        *,
        client: ApiClient | None = None,
        vision_client: ApiClient | None = None,
        **_ignored: Any,
    ) -> None:
        self.config = config
        self.client = client or _api_client_from_config(config)
        self.vision_client = vision_client

    @classmethod
    def from_config(cls, config, **kwargs) -> "ApiRuntime":
        return cls(config, **kwargs)

    def ask(
        self,
        text: str,
        *,
        channel: str = "api",
        include_recall: bool = True,
        recall_context: "RecallContext | None" = None,
        extra_context: str = "",
        image_attachments: list[dict[str, Any]] | None = None,
        on_tool_event: "Callable[[dict[str, Any]], None] | None" = None,
    ) -> ApiRuntimeResult:
        """Run an API model call and return structured result only."""
        clean = text.strip()
        if not clean:
            raise ValueError("missing text")

        started_at = time.perf_counter()
        recall_fragments = recall_context.count if recall_context is not None else 0

        prompt_started_at = time.perf_counter()
        messages = PromptAssembler(self.config).build_messages(
            clean,
            channel=channel,
            include_recall=include_recall,
            recall_context=recall_context,
            extra_context=extra_context,
        )
        transcript_start = max(0, len(messages) - 1)
        prompt_ready_at = time.perf_counter()
        model = self.config.api_model
        usage_total: dict[str, Any] = {}
        image_blocks = _image_attachment_blocks(self.config, image_attachments)
        image_model_override = os.environ.get("FIAM_API_IMAGE_MODEL", "").strip()
        has_image_input = False
        if image_blocks:
            if image_model_override or _model_supports_images(model):
                has_image_input = _attach_image_blocks_to_last_user_message(messages, image_blocks)
                model = image_model_override or model
            else:
                description = self._describe_images(clean, image_blocks, usage_total)
                _append_image_description_to_last_user_message(messages, description)

        tools_enabled = bool(getattr(self.config, "api_tools_enabled", False))
        tools = TOOL_SCHEMAS if tools_enabled and not has_image_input else None
        max_loops = max(1, int(getattr(self.config, "api_tools_max_loops", 10)))

        # Forward extended-thinking config into the API request. The catalog
        # entry sets these (see fiam.toml [catalog.<family>]). Providers that
        # ignore unknown fields stay unaffected. Set FIAM_API_REASONING_OFF=1
        # to disable if the upstream rejects them.
        extended_thinking_on = (
            bool(getattr(self.config, "extended_thinking", False))
            and os.environ.get("FIAM_API_REASONING_OFF", "").strip().lower() not in {"1", "true", "yes", "on"}
        )
        thinking_budget = int(getattr(self.config, "budget_tokens", 0) or 0) if extended_thinking_on else 0
        reasoning_effort = os.environ.get("FIAM_API_REASONING_EFFORT", "").strip() or ("high" if extended_thinking_on else "")

        executed_calls: list[dict[str, Any]] = []
        loops = 0
        provider_ms_total = 0
        completion: ApiCompletion | None = None
        thinking_chunks: list[str] = []
        while True:
            loops += 1
            provider_started_at = time.perf_counter()
            completion = self.client.complete(
                messages=messages,
                model=model,
                temperature=self.config.api_temperature,
                max_tokens=self.config.api_max_tokens,
                tools=tools,
                reasoning_effort=reasoning_effort,
                thinking_budget_tokens=thinking_budget,
            )
            if completion.reasoning:
                thinking_chunks.append(completion.reasoning)
            provider_ms_total += int((time.perf_counter() - provider_started_at) * 1000)
            _merge_usage(usage_total, completion.usage)
            if not completion.tool_calls:
                break
            # Append the assistant message verbatim so the next request preserves
            # the tool_call_ids the model issued.
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": completion.text or "",
                "tool_calls": completion.tool_calls,
            }
            messages.append(assistant_msg)
            for call in completion.tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                raw_args = str(fn.get("arguments") or "{}")
                call_id = str(call.get("id") or "")
                summary = _summarize_tool_input(name, raw_args)
                if on_tool_event:
                    on_tool_event({"event": "tool_use", "tool_use_id": call_id, "tool_name": name, "input_summary": summary})
                result = execute_tool_call(self.config, name, raw_args)
                bounded_result, result_object_hash, result_size = _bounded_tool_result(self.config, result)
                executed_calls.append({
                    "id": call_id,
                    "name": name,
                    "arguments": raw_args,
                    "result_preview": bounded_result[:300],
                    "result_object_hash": result_object_hash,
                    "result_size": result_size,
                    "loop": loops,
                })
                if on_tool_event:
                    on_tool_event({"event": "tool_result", "tool_use_id": call_id, "tool_name": name, "result_summary": bounded_result[:300], "is_error": False})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": bounded_result,
                })
            if loops >= max_loops:
                # Force one final no-tools call so the model emits a user-facing reply.
                provider_started_at = time.perf_counter()
                completion = self.client.complete(
                    messages=messages,
                    model=model,
                    temperature=self.config.api_temperature,
                    max_tokens=self.config.api_max_tokens,
                    tools=None,
                    reasoning_effort=reasoning_effort,
                    thinking_budget_tokens=thinking_budget,
                )
                provider_ms_total += int((time.perf_counter() - provider_started_at) * 1000)
                _merge_usage(usage_total, completion.usage)
                if completion.reasoning:
                    thinking_chunks.append(completion.reasoning)
                break

        assert completion is not None
        reply_text = completion.text or "(empty reply)"
        transcript_messages = [
            clean_message
            for message in [*messages[transcript_start:], {"role": "assistant", "content": reply_text}]
            if (clean_message := _valid_transcript_message(message)) is not None
        ]

        thinking_blocks: list[dict[str, Any]] = [
            {"text": chunk} for chunk in thinking_chunks if chunk
        ]

        return ApiRuntimeResult(
            ok=True,
            backend="api",
            reply=reply_text,
            model=completion.model,
            usage=usage_total or completion.usage,
            recall_fragments=recall_fragments,
            dispatched=0,
            raw=completion.raw,
            tool_loops=loops,
            tool_calls=executed_calls,
            transcript_messages=transcript_messages,
            timings={
                "prompt_build_ms": int((prompt_ready_at - prompt_started_at) * 1000),
                "provider_ms": provider_ms_total,
                "total_ms": int((time.perf_counter() - started_at) * 1000),
            },
            thinking_blocks=thinking_blocks,
        )

    def _describe_images(self, user_text: str, image_blocks: list[dict[str, Any]], usage_total: dict[str, Any]) -> str:
        vision_config = _vision_api_config(self.config)
        vision_client = self.vision_client or _api_client_from_config(vision_config)
        messages = [
            {
                "role": "system",
                "content": "You describe images for Fiam before a text-only model continues the task. Be concise, factual, and include visible text, objects, place cues, and dates if present. Reply in the user's language.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Describe these images for the next model. User request: {user_text}"},
                    *image_blocks,
                ],
            },
        ]
        completion = vision_client.complete(
            messages=messages,
            model=vision_config.api_model,
            temperature=0.0,
            max_tokens=min(int(getattr(self.config, "api_max_tokens", 1024)), 1024),
            tools=None,
        )
        _merge_usage(usage_total, completion.usage)
        return completion.text or "(no image description)"

