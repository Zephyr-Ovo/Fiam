"""Claude Code channel turn runner.

Runs one interactive Claude Code process per turn and injects the user turn via
the official ``claude/channel`` MCP notification during session startup.
Results are reconstructed from Claude Code's JSONL transcript.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fiam_lib.jsonl import _claude_projects_dir, _sanitize_home_path


CHANNEL_SERVER_NAME = "fiam-channel"
CHANNEL_FAILURE_PREFIX = "__fiam_cc_channel_failed__:"
_CHANNEL_TAG_RE = re.compile(r"^\s*<channel\b[^>]*>\s*(.*?)\s*</channel>\s*$", re.DOTALL)
_CHANNEL_TOKENS = ("<channel", '"origin":{"kind":"channel"', '"kind":"channel"')
_HOOK_TOKENS = ("hook_additional_context", "<user-prompt-submit-hook")
_HOOK_TAG_RE = re.compile(r"\s*<user-prompt-submit-hook\b[^>]*>.*?(?:</user-prompt-submit-hook>|\Z)\s*", re.DOTALL)


@dataclass
class ChannelTurn:
    stdout: str
    stderr: str = ""
    returncode: int = 0
    session_id: str = ""
    transcript_path: Path | None = None


def channel_supported() -> bool:
    return os.name == "posix"


def channel_enabled() -> bool:
    value = os.environ.get("FIAM_CC_TRANSPORT", "print").strip().lower()
    return value in {"channel", "channels", "cc-channel", ""} and channel_supported()


def project_transcript_path(home_path: Path, session_id: str) -> Path:
    return _claude_projects_dir() / _sanitize_home_path(home_path) / f"{session_id}.jsonl"


def scrub_transcript(path: Path | None) -> bool:
    """Remove hook payloads and unwrap official channel user rows in-place."""
    if not path or not path.exists():
        return False
    changed = False
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with path.open("r", encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
            for raw_line in src:
                if not raw_line.strip():
                    dst.write(raw_line)
                    continue
                if not any(token in raw_line for token in (*_HOOK_TOKENS, *_CHANNEL_TOKENS)):
                    dst.write(raw_line)
                    continue
                try:
                    data = json.loads(raw_line)
                except json.JSONDecodeError:
                    tmp.unlink(missing_ok=True)
                    return False
                clean, item_changed = _scrub_obj(data)
                if clean is None:
                    changed = True
                    continue
                changed = changed or item_changed
                dst.write(json.dumps(clean, ensure_ascii=False) + "\n" if item_changed else raw_line)
        if not changed:
            tmp.unlink(missing_ok=True)
            return False
        backup = path.with_name(path.name + ".fiam-context.bak")
        shutil.copy2(path, backup)
        tmp.replace(path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def run_channel_turn(
    config: Any,
    user_prompt: str,
    *,
    system_context: str = "",
    resume_session_id: str = "",
    max_turns: int = 10,
    timeout_seconds: float = 240.0,
) -> ChannelTurn:
    if not channel_supported():
        raise RuntimeError("Claude Code channel transport requires a POSIX PTY")

    session_id = resume_session_id.strip() or str(uuid.uuid4())
    transcript = project_transcript_path(config.home_path, session_id)
    scrub_transcript(transcript)
    start_offset = transcript.stat().st_size if transcript.exists() else 0
    request_id = f"req_{uuid.uuid4().hex[:16]}"

    with tempfile.TemporaryDirectory(prefix="fiam-cc-channel-") as tmp_dir:
        tmp = Path(tmp_dir)
        initial_file = tmp / "initial.json"
        initial_file.write_text(json.dumps({
            "content": user_prompt,
            "meta": {
                "request_id": request_id,
                "kind": "turn",
            },
        }, ensure_ascii=False), encoding="utf-8")
        mcp_config = tmp / "mcp.json"
        server_path = Path(config.code_path) / "channels" / "cc-channel" / "server.mjs"
        node_modules = server_path.parent / "node_modules" / "@modelcontextprotocol" / "sdk"
        if not node_modules.exists():
            raise RuntimeError(f"missing channel dependency; run npm install in {server_path.parent}")
        mcp_config.write_text(json.dumps({
            "mcpServers": {
                CHANNEL_SERVER_NAME: {
                    "command": "node",
                    "args": [str(server_path)],
                    "env": {
                        "FIAM_CC_CHANNEL_INITIAL_FILE": str(initial_file),
                        "FIAM_CC_CHANNEL_NAME": CHANNEL_SERVER_NAME,
                    },
                },
            },
        }, ensure_ascii=False), encoding="utf-8")

        cmd = _channel_command(
            config,
            session_id=session_id,
            resume_session_id=resume_session_id,
            system_context=system_context,
            mcp_config=mcp_config,
            max_turns=max_turns,
        )
        proc, master_fd = _spawn_pty(cmd, cwd=Path(config.home_path))
        stop_pump = threading.Event()
        pty_tail: list[str] = []
        pump = _start_pty_pump(master_fd, stop_pump, pty_tail)
        try:
            result = _wait_for_result(
                transcript,
                start_offset=start_offset,
                request_id=request_id,
                session_id=session_id,
                timeout_seconds=timeout_seconds,
                pty_tail=pty_tail,
            )
            return result
        finally:
            stop_pump.set()
            pump.join(timeout=1)
            _terminate_process(proc)
            try:
                os.close(master_fd)
            except OSError:
                pass
            scrub_transcript(transcript)


def _channel_command(
    config: Any,
    *,
    session_id: str,
    resume_session_id: str,
    system_context: str,
    mcp_config: Path,
    max_turns: int,
) -> list[str]:
    cmd = [
        "claude",
        "--dangerously-load-development-channels",
        f"server:{CHANNEL_SERVER_NAME}",
        "--mcp-config",
        str(mcp_config),
        "--setting-sources",
        "user,project,local",
        "--exclude-dynamic-system-prompt-sections",
        "--permission-mode",
        "bypassPermissions",
        "--max-turns",
        str(max_turns),
    ]
    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])
    else:
        cmd.extend(["--session-id", session_id])
    if system_context:
        cmd.extend(["--append-system-prompt", system_context])
    if getattr(config, "cc_model", ""):
        cmd.extend(["--model", config.cc_model])
    if getattr(config, "cc_disallowed_tools", ""):
        tools = [t.strip() for t in config.cc_disallowed_tools.split(",") if t.strip()]
        if tools:
            cmd.extend(["--disallowedTools", *tools])
    return cmd


def _spawn_pty(cmd: list[str], *, cwd: Path):
    import pty

    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(cwd),
        text=False,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)
    return proc, master_fd


def _drive_startup_prompts(master_fd: int, *, timeout_seconds: float) -> None:
    os.set_blocking(master_fd, False)
    deadline = time.monotonic() + timeout_seconds
    buffer = ""
    while time.monotonic() < deadline:
        try:
            chunk = os.read(master_fd, 4096).decode("utf-8", errors="ignore")
        except BlockingIOError:
            chunk = ""
        except OSError:
            return
        if chunk:
            buffer = (buffer + chunk)[-4000:]
            low = buffer.lower()
            if (
                ("dangerously" in low and ("continue" in low or "yes" in low or "confirm" in low))
                or ("loading" in low and "development" in low and "channels" in low)
            ):
                try:
                    os.write(master_fd, b"\r\n")
                except OSError:
                    return
            if "blocked by org policy" in low:
                raise RuntimeError("Claude Code channel blocked by organization policy")
        time.sleep(0.05)


def _start_pty_pump(master_fd: int, stop: threading.Event, tail: list[str]) -> threading.Thread:
    os.set_blocking(master_fd, False)

    def run() -> None:
        buffer = ""
        confirm_until = 0.0
        last_confirm = 0.0
        while not stop.is_set():
            try:
                chunk = os.read(master_fd, 4096).decode("utf-8", errors="ignore")
            except BlockingIOError:
                now = time.monotonic()
                if now < confirm_until and now - last_confirm > 0.25:
                    try:
                        os.write(master_fd, b"1\r")
                    except OSError:
                        return
                    last_confirm = now
                time.sleep(0.05)
                continue
            except OSError:
                return
            if not chunk:
                time.sleep(0.05)
                continue
            buffer = (buffer + chunk)[-4000:]
            tail.append(chunk)
            if len(tail) > 200:
                del tail[:100]
            low = buffer.lower()
            if (
                ("dangerously" in low and ("continue" in low or "yes" in low or "confirm" in low))
                or ("loading" in low and "development" in low and "channels" in low)
            ):
                confirm_until = time.monotonic() + 3.0
            if "do you trust" in low or "trust the files" in low or ("quick" in low and "safety" in low and "trust" in low):
                confirm_until = time.monotonic() + 3.0

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _wait_for_result(
    transcript: Path,
    *,
    start_offset: int,
    request_id: str,
    session_id: str,
    timeout_seconds: float,
    pty_tail: list[str] | None = None,
) -> ChannelTurn:
    deadline = time.monotonic() + timeout_seconds
    seen_user = False
    parsed: list[dict] = []
    safe_offset = start_offset
    while time.monotonic() < deadline:
        if transcript.exists() and transcript.stat().st_size > safe_offset:
            new_rows, safe_offset = _read_rows_from(transcript, safe_offset)
            for row in new_rows:
                if _is_matching_channel_user(row, request_id):
                    seen_user = True
                    parsed = [row]
                    continue
                if seen_user:
                    parsed.append(row)
                    if _is_final_assistant(row):
                        return _rows_to_turn(parsed, session_id=session_id, transcript=transcript)
            if not seen_user:
                time.sleep(0.05)
                continue
        time.sleep(0.1)
    tail = "".join((pty_tail or [])[-40:]).strip()
    detail = f"; pty_tail={tail[-1200:]}" if tail else ""
    raise RuntimeError(f"claude channel turn timeout{detail}")


def _read_rows_from(path: Path, offset: int) -> tuple[list[dict], int]:
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(offset)
        raw = f.read()
    rows: list[dict] = []
    safe = offset
    pos = 0
    for raw_line in raw.split(b"\n"):
        line_end = pos + len(raw_line) + 1
        pos = line_end
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            safe = min(offset + pos, size)
            continue
        try:
            rows.append(json.loads(text))
        except json.JSONDecodeError:
            break
        safe = min(offset + pos, size)
    return rows, safe


def _is_matching_channel_user(row: dict, request_id: str) -> bool:
    if row.get("type") != "user":
        return False
    if ((row.get("origin") or {}).get("kind")) != "channel":
        return False
    content = ((row.get("message") or {}).get("content"))
    return isinstance(content, str) and f'request_id="{request_id}"' in content


def _is_final_assistant(row: dict) -> bool:
    if row.get("type") != "assistant":
        return False
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    return message.get("stop_reason") == "end_turn"


def _rows_to_turn(rows: list[dict], *, session_id: str, transcript: Path) -> ChannelTurn:
    stream_items: list[dict] = []
    final_text = ""
    final_model = ""
    usage: dict[str, Any] | None = None
    started = _row_ts(rows[0]) if rows else 0.0
    ended = started
    for row in rows:
        ended = max(ended, _row_ts(row))
        if row.get("type") == "assistant":
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            final_model = str(message.get("model") or final_model)
            if isinstance(message.get("usage"), dict):
                usage = message.get("usage")
            stream_items.append({"type": "assistant", "message": message})
            text = _assistant_text(message)
            if text:
                final_text = text
        elif row.get("type") == "user":
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            content = message.get("content")
            if isinstance(content, list):
                stream_items.append({"type": "user", "message": message, "tool_use_result": row.get("toolUseResult")})
    stream_items.append({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": final_text,
        "session_id": session_id,
        "model": final_model,
        "usage": usage or {},
        "duration_ms": max(0, int((ended - started) * 1000)) if started and ended else 0,
        "total_cost_usd": 0,
    })
    return ChannelTurn(
        stdout="".join(json.dumps(item, ensure_ascii=False) + "\n" for item in stream_items),
        stderr="",
        returncode=0,
        session_id=session_id,
        transcript_path=transcript,
    )


def _assistant_text(message: dict) -> str:
    parts = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _row_ts(row: dict) -> float:
    text = str(row.get("timestamp") or "")
    if not text:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _scrub_obj(data: Any) -> tuple[Any | None, bool]:
    clean, changed = _scrub_hook_value(data)
    if not isinstance(clean, dict):
        return clean, changed
    if clean.get("type") == "attachment":
        attachment = clean.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "hook_additional_context":
            return None, True
    unwrapped, channel_changed = _scrub_channel_user(clean)
    return unwrapped, changed or channel_changed


def _scrub_hook_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        if "<user-prompt-submit-hook" not in value:
            return value, False
        scrubbed = _HOOK_TAG_RE.sub("\n", value).strip()
        return scrubbed, scrubbed != value
    if isinstance(value, list):
        changed = False
        out = []
        for item in value:
            clean, item_changed = _scrub_hook_value(item)
            out.append(clean)
            changed = changed or item_changed
        return out, changed
    if isinstance(value, dict):
        changed = False
        out = {}
        for key, item in value.items():
            clean, item_changed = _scrub_hook_value(item)
            out[key] = clean
            changed = changed or item_changed
        return out, changed
    return value, False


def _scrub_channel_user(row: dict) -> tuple[dict, bool]:
    if row.get("type") not in {"user", "queue-operation"}:
        return row, False
    if row.get("type") == "queue-operation" and isinstance(row.get("content"), str):
        match = _CHANNEL_TAG_RE.match(row["content"])
        if match:
            clean = dict(row)
            clean["content"] = match.group(1).strip()
            return clean, True
        return row, False
    if ((row.get("origin") or {}).get("kind")) != "channel":
        return row, False
    message = row.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return row, False
    match = _CHANNEL_TAG_RE.match(message["content"])
    if not match:
        return row, False
    clean = dict(row)
    clean_msg = dict(message)
    clean_msg["content"] = match.group(1).strip()
    clean["message"] = clean_msg
    clean.pop("origin", None)
    clean.pop("isMeta", None)
    return clean, True


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def as_completed_process(turn: ChannelTurn) -> SimpleNamespace:
    return SimpleNamespace(stdout=turn.stdout, stderr=turn.stderr, returncode=turn.returncode)
