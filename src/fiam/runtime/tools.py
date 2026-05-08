"""Function-calling tool schemas + local executors for the API runtime.

Sandboxing: every path argument is resolved relative to ``config.home_path``
(``~/fiet-home``) and rejected if it escapes that root. The model can read,
list, and edit files inside its home, run ``git diff`` against it, but cannot
touch the rest of the filesystem.

Tool surface (deliberately small, mirrors editor primitives):

- ``read_file(path)``                 — read entire file
- ``list_dir(path)``                  — list directory entries
- ``str_replace(path, old, new)``     — replace exactly one occurrence
- ``insert(path, line, content)``     — insert after ``line`` (0 = file head)
- ``create_file(path, content)``      — create new file, fail if exists
- ``git_diff(path?, since?)``         — git diff inside home_path
- ``grep_files(path, query)``         — search text files under a path
- ``add_todo(at, kind, reason?)`` — append a wake/todo entry to self/todo.jsonl
- ``set_ai_state(state, until?, reason?)`` — update self/ai_state.json

The ``remember`` action is intentionally NOT a separate tool: editing
``self/identity.md`` etc. is just ``str_replace`` on a known path.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fiam.config import FiamConfig


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the entire contents of a UTF-8 text file inside your home directory. This cannot inspect image or binary files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from home, e.g. 'self/identity.md'."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries in a directory inside your home.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from home, '.' for root."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": (
                "Replace exactly one occurrence of `old` with `new` in a file. "
                "Fails if `old` appears zero or multiple times. Use this to edit "
                "a section of self/*.md without rewriting the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string", "description": "Exact text to find. Must be unique."},
                    "new": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert",
            "description": "Insert `content` after line `line` (0 = before first line).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer", "minimum": 0},
                    "content": {"type": "string"},
                },
                "required": ["path", "line", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file. Fails if the file already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Run `git diff` inside your home directory. Optional path narrows the diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to limit the diff (optional)."},
                    "since": {"type": "string", "description": "Optional revision (e.g. 'HEAD~3')."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": (
                "Search UTF-8 text files under a file or directory inside your home. "
                "Use this for uploaded files instead of reading large files in full."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute path inside home, e.g. 'uploads'."},
                    "query": {"type": "string", "description": "Literal text to search for."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["path", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": (
                "Append a future wake/todo to self/todo.jsonl. Use kind='wake' for "
                "a bare time-only reminder (no description – you'll re-read your "
                "session memory when you wake up); use kind='todo' to attach a "
                "short note describing what you wanted to do at that time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "at": {"type": "string", "description": "ISO timestamp with timezone, or 'YYYY-MM-DD HH:MM' (project timezone)."},
                    "kind": {"type": "string", "enum": ["wake", "todo"]},
                    "reason": {"type": "string", "description": "Required when kind='todo'. Ignored when kind='wake'."},
                },
                "required": ["at", "kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_ai_state",
            "description": "Switch your current app-visible state in self/ai_state.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "enum": ["notify", "mute", "block", "sleep", "busy", "together", "online"]},
                    "until": {"type": "string", "description": "Optional ISO timestamp or 'open' for sleep/busy states."},
                    "reason": {"type": "string"},
                },
                "required": ["state"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Sandbox + executors
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Raised on bad tool arguments or sandbox violations."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    call_id: str
    content: str  # already JSON-serialized when needed


def _resolve(home: Path, rel: str) -> Path:
    if not isinstance(rel, str):
        raise ToolError("path must be a string")
    candidate = (home / rel).resolve()
    home_resolved = home.resolve()
    try:
        candidate.relative_to(home_resolved)
    except ValueError as exc:
        raise ToolError(f"path escapes home: {rel!r}") from exc
    return candidate


def _read_file(home: Path, args: dict[str, Any]) -> str:
    path = _resolve(home, args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {args['path']}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "error: file is binary or not UTF-8 text; read_file cannot inspect image/binary contents"


def _list_dir(home: Path, args: dict[str, Any]) -> str:
    path = _resolve(home, args["path"])
    if not path.is_dir():
        raise ToolError(f"not a directory: {args['path']}")
    entries = []
    for child in sorted(path.iterdir()):
        kind = "dir" if child.is_dir() else "file"
        size = child.stat().st_size if child.is_file() else None
        entries.append({"name": child.name, "type": kind, "size": size})
    return json.dumps(entries, ensure_ascii=False)


def _str_replace(home: Path, args: dict[str, Any]) -> str:
    path = _resolve(home, args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {args['path']}")
    old = args["old"]
    new = args["new"]
    if not isinstance(old, str) or not isinstance(new, str):
        raise ToolError("old and new must be strings")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ToolError("old string not found")
    if count > 1:
        raise ToolError(f"old string matches {count} times; must be unique")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return "ok"


def _insert(home: Path, args: dict[str, Any]) -> str:
    path = _resolve(home, args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {args['path']}")
    line = int(args["line"])
    content = args["content"]
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line < 0 or line > len(lines):
        raise ToolError(f"line {line} out of range (0..{len(lines)})")
    insertion = content if content.endswith("\n") else content + "\n"
    lines.insert(line, insertion)
    path.write_text("".join(lines), encoding="utf-8")
    return "ok"


def _create_file(home: Path, args: dict[str, Any]) -> str:
    path = _resolve(home, args["path"])
    if path.exists():
        raise ToolError(f"file already exists: {args['path']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = args["content"]
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    path.write_text(content, encoding="utf-8")
    return "ok"


def _git_diff(home: Path, args: dict[str, Any]) -> str:
    cmd = ["git", "-C", str(home), "diff"]
    since = args.get("since")
    if since:
        cmd.append(str(since))
    rel = args.get("path")
    if rel:
        _resolve(home, rel)  # sandbox check
        cmd.extend(["--", rel])
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"git diff failed: {exc}") from exc
    if out.returncode != 0 and out.stderr:
        raise ToolError(f"git: {out.stderr.strip()[:300]}")
    return out.stdout[:8000] or "(no diff)"


def _grep_files(home: Path, args: dict[str, Any]) -> str:
    root = _resolve(home, args["path"])
    query = args["query"]
    if not isinstance(query, str) or not query:
        raise ToolError("query must be a non-empty string")
    max_results = max(1, min(50, int(args.get("max_results", 20))))
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    results: list[dict[str, Any]] = []
    for path in files:
        if len(results) >= max_results:
            break
        try:
            rel = path.resolve().relative_to(home.resolve()).as_posix()
        except ValueError:
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(lines, start=1):
            if query in line:
                results.append({"path": rel, "line": idx, "text": line[:500]})
                if len(results) >= max_results:
                    break
    return json.dumps(results, ensure_ascii=False)


def _add_todo(home: Path, args: dict[str, Any], default_tz: Any = None) -> str:
    at_raw = str(args["at"]).strip()
    kind = str(args["kind"]).strip().lower()
    reason = str(args.get("reason") or "").strip()
    if kind not in {"wake", "todo"}:
        raise ToolError("kind must be 'wake' or 'todo'")
    if kind == "todo" and not reason:
        raise ToolError("reason is required when kind='todo'")
    parsed: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(at_raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(at_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolError("at must be ISO timestamp or 'YYYY-MM-DD HH:MM'") from exc
    if parsed.tzinfo is None:
        # naive timestamps are interpreted in the project timezone (per schema)
        parsed = parsed.replace(tzinfo=default_tz or timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise ToolError("at must be in the future")
    path = home / "self" / "todo.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": parsed.isoformat(),
        "kind": kind,
        "reason": reason if kind == "todo" else "",
        "created": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return json.dumps({"ok": True, **record}, ensure_ascii=False)


def _set_ai_state(home: Path, args: dict[str, Any]) -> str:
    state = str(args["state"]).strip().lower()
    if state not in {"notify", "mute", "block", "sleep", "busy", "together", "online"}:
        raise ToolError("invalid state")
    record: dict[str, Any] = {
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    until = str(args.get("until") or "").strip()
    if until:
        if until != "open":
            try:
                parsed = datetime.fromisoformat(until)
            except ValueError as exc:
                raise ToolError("until must be ISO timestamp or 'open'") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            until = parsed.isoformat()
        record["until"] = until
    reason = str(args.get("reason") or "").strip()
    if reason:
        record["reason"] = reason
    path = home / "self" / "ai_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.dumps({"ok": True, **record}, ensure_ascii=False)


_DISPATCH = {
    "read_file": _read_file,
    "list_dir": _list_dir,
    "str_replace": _str_replace,
    "insert": _insert,
    "create_file": _create_file,
    "git_diff": _git_diff,
    "grep_files": _grep_files,
    "add_todo": _add_todo,
    "set_ai_state": _set_ai_state,
}


def execute_tool_call(config: "FiamConfig", name: str, raw_args: str) -> str:
    """Execute one tool call, return content string for the tool message.

    Errors are returned as ``"error: ..."`` strings so the model can recover
    rather than the whole loop crashing.
    """
    handler = _DISPATCH.get(name)
    if handler is None:
        return f"error: unknown tool {name!r}"
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as exc:
        return f"error: invalid JSON arguments ({exc})"
    # Some models (e.g. deepseek via openrouter) double-encode args as a JSON
    # string. Unwrap one extra layer if needed.
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            pass
    if not isinstance(args, dict):
        return "error: arguments must be a JSON object"
    try:
        if name == "add_todo":
            tzinfo = None
            try:
                tzinfo = config.project_tz()
            except Exception:
                tzinfo = None
            return _add_todo(config.home_path, args, default_tz=tzinfo)
        return handler(config.home_path, args)
    except ToolError as exc:
        return f"error: {exc}"
    except KeyError as exc:
        return f"error: missing argument {exc}"
    except OSError as exc:
        return f"error: {exc}"
