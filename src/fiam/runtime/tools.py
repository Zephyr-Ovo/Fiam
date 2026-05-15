"""Function-calling tool schemas + local executors for the API runtime.

Sandboxing: every path argument is resolved relative to ``config.home_path``
(the AI's project home) and rejected if it escapes that root. The model can
read, list, and edit files inside its home, run ``git diff`` against it, but
cannot touch the rest of the filesystem.

Tool surface (deliberately small, mirrors editor primitives):

- ``Read(path)``                      — read entire file (Claude Code parity)
- ``Write(path, content)``            — create new file, fail if exists
- ``Edit(path, old_string, new_string, replace_all?)`` — edit a file in place
- ``Glob(pattern, path?)``            — list files matching glob, mtime sorted
- ``Grep(path, query)``               — search text files under a path
- ``Bash(command, timeout?)``         — run a shell command (full freedom; CC parity)
- ``ObjectSave(content, name?)``      — store generated text as an ObjectStore object
- ``ObjectImport(path, name?)``       — import a file from home into ObjectStore
- ``git_diff(path?, since?)``         — git diff inside home_path

For delayed wakes/todos and AI state changes, the API runtime relies on
XML markers in plain text (``<todo at=...>``, ``<wake>``, ``<sleep until=>``,
``<mute />``, ``<notify />``), parsed in ``_record_assistant`` — same path
the CC runtime uses. They do not occupy a tool_call slot, do not break
prefix cache, and are taught to the model via the awareness prompt.

Note on freedom: ``Bash`` runs without sandbox by design (CC-like free agent
per docs/ai_runtime_alignment_notes.md). Path-based tools (Read/Write/Edit/
Glob/Grep/git_diff) keep the home_path sandbox because they are scoped to AI
memory editing; if AI needs to touch the wider filesystem it uses ``Bash``.

The ``remember`` action is intentionally NOT a separate tool: editing
``self/identity.md`` etc. is just ``Edit`` on a known path.
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import time
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
            "name": "Read",
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
            "name": "Edit",
            "description": (
                "Edit a file by replacing `old_string` with `new_string`. "
                "By default fails if `old_string` is not exactly unique in the "
                "file. Set `replace_all=true` to replace every occurrence. "
                "To insert at file head, pass `old_string=''` with the new "
                "file head text plus the existing first line as `new_string`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from home."},
                    "old_string": {"type": "string", "description": "Exact text to find. Must be unique unless replace_all=true."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": (
                "List files matching a glob pattern, sorted by modification "
                "time (newest first). Use this instead of listing whole "
                "directories. Returns at most 200 paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.md' or 'self/*.md'."},
                    "path": {"type": "string", "description": "Optional search root relative to home (default: home itself)."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Write",
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
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
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
            "name": "ObjectSearch",
            "description": (
                "Search uploaded/attached ObjectStore objects by name, mime, tag, summary, hash, or obj token. "
                "Use this to resolve short obj:<prefix> references before sending attachments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text. Empty returns recent objects."},
                    "token": {"type": "string", "description": "Optional obj:<prefix> or full object hash to resolve."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ObjectSave",
            "description": (
                "Store generated UTF-8 text in ObjectStore and return an obj:<token> reference. "
                "Use this before attaching generated text/files in <send attach=...>; do not send paths or raw file bodies."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Text content to store."},
                    "name": {"type": "string", "description": "Suggested filename, e.g. notes.txt."},
                    "mime": {"type": "string", "description": "MIME type, default text/plain."},
                    "summary": {"type": "string", "description": "Short searchable summary."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Search tags."},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ObjectImport",
            "description": (
                "Import a file inside your home directory into ObjectStore and return an obj:<token> reference. "
                "Use this after Bash downloads or generates a file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path inside home."},
                    "name": {"type": "string", "description": "Optional attachment filename override."},
                    "mime": {"type": "string", "description": "Optional MIME override."},
                    "summary": {"type": "string", "description": "Short searchable summary."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Search tags."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": (
                "Run a shell command and return its combined stdout+stderr. "
                "Runs without sandbox: cwd is the project home but absolute "
                "paths and any binary on PATH are reachable. Use this for "
                "git, build, test, fiam CLI, file ops outside the project "
                "home, or any task not covered by the Read/Write/Edit/Grep "
                "tools. Long-running commands are killed at the timeout."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "timeout": {"type": "integer", "description": "Seconds before forced kill (default 120, max 600).", "minimum": 1, "maximum": 600},
                    "description": {"type": "string", "description": "Short human-readable label for logs (optional)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Recall",
            "description": (
                "Retrieve a time-decayed summary from the Studio track system. "
                "Available names: 'edit' (vault editing activity), 'work' (code repo commits), "
                "'system' (runtime phases/traces). Recent content is returned in full; "
                "older content is progressively folded (7d full → 30d headings → 90d titles → omitted)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Track name: 'edit', 'work', or 'system'."},
                    "since": {"type": "string", "description": "Optional ISO datetime; hide sections older than this."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_list",
            "description": "List all books on the shared bookshelf.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_toc",
            "description": "Get the table of contents for a book.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "string", "description": "Book identifier."},
                },
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_read",
            "description": "Read paragraphs from a specific chapter. Updates the reading cursor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "string"},
                    "chapter": {"type": "integer", "description": "Chapter index (from book_toc)."},
                    "start": {"type": "integer", "description": "Starting paragraph index (default 0)."},
                    "count": {"type": "integer", "description": "Number of paragraphs to read (default 20)."},
                },
                "required": ["book_id", "chapter"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_next",
            "description": "Continue reading from the current position. Advances the cursor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of paragraphs (default 20)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_prev",
            "description": "Go back and re-read previous paragraphs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of paragraphs (default 20)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_search",
            "description": "Search for text across a book's paragraphs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "string"},
                    "query": {"type": "string", "description": "Text to search for."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["book_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_annotate",
            "description": "Leave an annotation on a specific paragraph. Both you and the human can annotate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "string"},
                    "paragraph_id": {"type": "string", "description": "Paragraph ID (from book_read output)."},
                    "text": {"type": "string", "description": "Your annotation or comment."},
                },
                "required": ["book_id", "paragraph_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_annotations",
            "description": "View annotations on a book, optionally filtered by chapter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "string"},
                    "chapter_id": {"type": "string", "description": "Optional chapter ID to filter (e.g. 'ch_0003')."},
                },
                "required": ["book_id"],
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
        return "error: file is binary or not UTF-8 text; Read cannot inspect image/binary contents"


def _edit(home: Path, args: dict[str, Any]) -> str:
    path = _resolve(home, args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {args['path']}")
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    if not isinstance(old, str) or not isinstance(new, str):
        raise ToolError("old_string and new_string must be strings")
    replace_all = bool(args.get("replace_all", False))
    text = path.read_text(encoding="utf-8")
    if old == "":
        # Convention: empty old_string means prepend new_string at file head.
        path.write_text(new + text, encoding="utf-8")
        return "ok"
    count = text.count(old)
    if count == 0:
        raise ToolError("old_string not found")
    if count > 1 and not replace_all:
        raise ToolError(f"old_string matches {count} times; pass replace_all=true or make it unique")
    if replace_all:
        path.write_text(text.replace(old, new), encoding="utf-8")
    else:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return "ok"


def _glob(home: Path, args: dict[str, Any]) -> str:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ToolError("pattern must be a non-empty string")
    rel = args.get("path", ".")
    root = _resolve(home, rel) if rel else home
    if not root.exists():
        raise ToolError(f"path does not exist: {rel!r}")
    if not root.is_dir():
        raise ToolError(f"path is not a directory: {rel!r}")
    matches: list[tuple[float, str]] = []
    home_resolved = home.resolve()
    for p in root.glob(pattern):
        if not p.is_file():
            continue
        try:
            rel_path = p.resolve().relative_to(home_resolved).as_posix()
        except ValueError:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        matches.append((mtime, rel_path))
    matches.sort(key=lambda x: x[0], reverse=True)
    out = [path for _, path in matches[:200]]
    return json.dumps(out, ensure_ascii=False)


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


def _object_search(config: "FiamConfig", args: dict[str, Any]) -> str:
    from fiam.store.object_catalog import ObjectCatalog

    catalog = ObjectCatalog.from_config(config)
    limit = max(1, min(50, int(args.get("limit", 20))))
    query = str(args.get("query") or "")
    token = str(args.get("token") or "")
    payload: dict[str, Any] = {
        "records": [record.to_dict() for record in catalog.search(query, limit=limit)],
    }
    if token:
        payload["object_hash"] = catalog.resolve_token(token)
    return json.dumps(payload, ensure_ascii=False)


def _object_save(config: "FiamConfig", args: dict[str, Any]) -> str:
    content = args.get("content")
    if not isinstance(content, str):
        raise ToolError("content must be a string")
    return _store_object_tool_result(
        config,
        content.encode("utf-8"),
        name=_clean_object_name(args.get("name"), default="generated.txt"),
        mime=str(args.get("mime") or "text/plain").strip() or "text/plain",
        summary=str(args.get("summary") or "").strip(),
        tags=_clean_tags(args.get("tags")),
        source="tool:ObjectSave",
    )


def _object_import(config: "FiamConfig", args: dict[str, Any]) -> str:
    path = _resolve(config.home_path, args["path"])
    if not path.is_file():
        raise ToolError(f"not a file: {args['path']}")
    name = _clean_object_name(args.get("name"), default=path.name)
    guessed_mime = mimetypes.guess_type(name)[0] or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _store_object_tool_result(
        config,
        path.read_bytes(),
        name=name,
        mime=str(args.get("mime") or guessed_mime).strip() or "application/octet-stream",
        summary=str(args.get("summary") or "").strip(),
        tags=_clean_tags(args.get("tags")),
        source="tool:ObjectImport",
    )


def _store_object_tool_result(
    config: "FiamConfig",
    data: bytes,
    *,
    name: str,
    mime: str,
    summary: str = "",
    tags: tuple[str, ...] = (),
    source: str,
) -> str:
    from fiam.store.beat import Beat, append_beat
    from fiam.store.objects import ObjectStore

    raw = bytes(data or b"")
    object_hash = ObjectStore(config.object_dir).put_bytes(raw, suffix="")
    meta: dict[str, Any] = {
        "name": "attachment",
        "object_hash": object_hash,
        "object_name": name,
        "object_mime": mime,
        "object_size": len(raw),
        "direction": "generated",
        "source": source,
        "visibility": "private",
    }
    if summary:
        meta["object_summary"] = summary
    if tags:
        meta["object_tags"] = list(tags)
    if getattr(config, "flow_path", None) is not None:
        append_beat(config.flow_path, Beat(
            t=datetime.now(timezone.utc),
            actor="ai",
            channel="tool",
            kind="attachment",
            content=f"object: {name}",
            meta=meta,
            surface="api",
        ))
    payload: dict[str, Any] = {
        "object_hash": object_hash,
        "token": f"obj:{object_hash[:12]}",
        "name": name,
        "mime": mime,
        "size": len(raw),
    }
    if summary:
        payload["summary"] = summary
    if tags:
        payload["tags"] = list(tags)
    return json.dumps(payload, ensure_ascii=False)


def _clean_object_name(value: Any, *, default: str) -> str:
    name = Path(str(value or default)).name.strip()
    return name or default


def _clean_tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = [item.strip() for item in value.replace(",", " ").split()]
    elif isinstance(value, list):
        raw = [str(item or "").strip() for item in value]
    else:
        raw = []
    tags: list[str] = []
    for item in raw:
        if item and item not in tags:
            tags.append(item)
    return tuple(tags[:20])


_BASH_DEFAULT_TIMEOUT = 120
_BASH_MAX_TIMEOUT = 600
_BASH_OUTPUT_LIMIT = 30_000


def _bash(home: Path, args: dict[str, Any]) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("command must be a non-empty string")
    timeout = args.get("timeout", _BASH_DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as exc:
        raise ToolError("timeout must be an integer") from exc
    timeout = max(1, min(_BASH_MAX_TIMEOUT, timeout))
    cwd = home if home.exists() else None
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        partial = (exc.stdout or "") + (exc.stderr or "")
        return json.dumps({
            "ok": False,
            "timeout": True,
            "timeout_seconds": timeout,
            "runtime_ms": elapsed_ms,
            "output": _truncate_bash_output(partial),
        }, ensure_ascii=False)
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    output = (completed.stdout or "") + (completed.stderr or "")
    return json.dumps({
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "runtime_ms": elapsed_ms,
        "output": _truncate_bash_output(output),
    }, ensure_ascii=False)


def _truncate_bash_output(text: str) -> str:
    if len(text) <= _BASH_OUTPUT_LIMIT:
        return text
    head = text[: _BASH_OUTPUT_LIMIT // 2]
    tail = text[-_BASH_OUTPUT_LIMIT // 2 :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n\n... [{omitted} chars omitted] ...\n\n{tail}"


def _recall(home: Path, args: dict) -> str:
    from fiam.track import recall as recall_fn
    from fiam.track.config import load_track_config
    name = str(args.get("name") or "").strip()
    if name not in ("edit", "work", "system"):
        return f"error: unknown track name {name!r} (available: edit, work, system)"
    cfg = load_track_config(home.parent / "fiam.toml")
    since_str = str(args.get("since") or "").strip()
    since = None
    if since_str:
        try:
            since = datetime.fromisoformat(since_str)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            return f"error: invalid since datetime: {since_str}"
    text = recall_fn(cfg.vault_dir, name, since=since)
    return text or "(no track data available)"


def _book_tool(config: "FiamConfig", name: str, args: dict[str, Any]) -> str:
    from fiam.bookshelf import Bookshelf

    shelf = Bookshelf(config.home_path / "bookshelf")
    try:
        if name == "book_list":
            return json.dumps(shelf.list_books(), ensure_ascii=False)
        if name == "book_toc":
            return json.dumps(shelf.toc(args["book_id"]), ensure_ascii=False)
        if name == "book_read":
            return json.dumps(shelf.read(
                args["book_id"],
                int(args["chapter"]),
                start=int(args.get("start", 0)),
                count=int(args.get("count", 20)),
            ), ensure_ascii=False)
        if name == "book_next":
            return json.dumps(shelf.read_next(count=int(args.get("count", 20))), ensure_ascii=False)
        if name == "book_prev":
            return json.dumps(shelf.read_prev(count=int(args.get("count", 20))), ensure_ascii=False)
        if name == "book_search":
            return json.dumps(shelf.search(
                args["book_id"],
                args["query"],
                max_results=int(args.get("max_results", 10)),
            ), ensure_ascii=False)
        if name == "book_annotate":
            return json.dumps(shelf.annotate(
                args["book_id"],
                args["paragraph_id"],
                args["text"],
                author="ai",
            ), ensure_ascii=False)
        if name == "book_annotations":
            return json.dumps(shelf.get_annotations(
                args["book_id"],
                chapter_id=args.get("chapter_id"),
            ), ensure_ascii=False)
    except (FileNotFoundError, IndexError, RuntimeError, KeyError) as exc:
        return f"error: {exc}"
    return f"error: unknown book tool {name!r}"


_BOOK_TOOLS = frozenset({
    "book_list", "book_toc", "book_read", "book_next", "book_prev",
    "book_search", "book_annotate", "book_annotations",
})

_DISPATCH = {
    # Claude Code parity names
    "Read": _read_file,
    "Write": _create_file,
    "Edit": _edit,
    "Glob": _glob,
    "Grep": _grep_files,
    "Bash": _bash,
    # fiam-specific tools (no CC counterpart yet; will migrate to fiam CLI)
    "git_diff": _git_diff,
    "Recall": _recall,
}


def execute_tool_call(config: "FiamConfig", name: str, raw_args: str) -> str:
    """Execute one tool call, return content string for the tool message.

    Errors are returned as ``"error: ..."`` strings so the model can recover
    rather than the whole loop crashing.
    """
    if name in _BOOK_TOOLS:
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as exc:
            return f"error: invalid JSON arguments ({exc})"
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        if not isinstance(args, dict):
            return "error: arguments must be a JSON object"
        return _book_tool(config, name, args)
    handler = _DISPATCH.get(name)
    if handler is None and name not in {"ObjectSearch", "ObjectSave", "ObjectImport"}:
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
    if name == "ObjectSearch":
        try:
            return _object_search(config, args)
        except (OSError, ValueError) as exc:
            return f"error: {exc}"
    if name == "ObjectSave":
        try:
            return _object_save(config, args)
        except (OSError, ValueError, ToolError, KeyError) as exc:
            return f"error: {exc}"
    if name == "ObjectImport":
        try:
            return _object_import(config, args)
        except (OSError, ValueError, ToolError, KeyError) as exc:
            return f"error: {exc}"
    try:
        return handler(config.home_path, args)
    except ToolError as exc:
        return f"error: {exc}"
    except KeyError as exc:
        return f"error: missing argument {exc}"
    except OSError as exc:
        return f"error: {exc}"
