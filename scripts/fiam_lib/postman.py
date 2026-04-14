"""
Outbox dispatcher — delivers AI's messages via Telegram or Email.

The AI writes Markdown files to home/outbox/ with YAML frontmatter:

    ---
    to: zephyr
    via: telegram        # telegram | email
    priority: normal     # normal | urgent
    ---

    Message body here...
    [sticker:猫咪哭]        ← optional sticker tag, sent as TG sticker

This module watches outbox/ and dispatches each file, then moves it
to outbox/sent/. It never touches the AI's conversation or CC session.
"""

from __future__ import annotations

import json
import os
import imaplib
import email as email_mod
import re
import shutil
import smtplib
import time
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import frontmatter

from fiam.config import FiamConfig


# ------------------------------------------------------------------
# Telegram
# ------------------------------------------------------------------

def _tg_send(token: str, chat_id: str, text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        return False


def _tg_send_sticker(token: str, chat_id: str, file_id: str) -> bool:
    """Send a sticker by file_id via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendSticker"
    payload = json.dumps({"chat_id": chat_id, "sticker": file_id}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        return False


def _tg_send_photo(token: str, chat_id: str, photo_path: Path) -> bool:
    """Send a local image file as photo via Telegram Bot API (multipart)."""
    import mimetypes
    boundary = "----FiamBoundary"
    mime = mimetypes.guess_type(str(photo_path))[0] or "image/webp"
    photo_data = photo_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="{photo_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        return False


# ------------------------------------------------------------------
# Sticker index (store/stickers/index.json)
# ------------------------------------------------------------------

def _sticker_dir(config: FiamConfig) -> Path:
    return config.code_path / "assets" / "stickers"


def _load_sticker_index(config: FiamConfig) -> dict:
    idx_path = _sticker_dir(config) / "index.json"
    if idx_path.exists():
        try:
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception:
            pass
    return {}


def _save_sticker_index(config: FiamConfig, index: dict) -> None:
    d = _sticker_dir(config)
    d.mkdir(parents=True, exist_ok=True)
    full = {"_说明": "表情包索引。file=本地文件, file_id=TG原生sticker"}
    full.update(index)
    (d / "index.json").write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_stickers(text: str, config: FiamConfig) -> tuple[str, list[dict]]:
    """Extract [sticker:名称] tags from text. Returns (cleaned_text, sticker_list)."""
    sticker_map = _load_sticker_index(config)
    stickers: list[dict] = []

    def replacer(match):
        name = match.group(1).strip()
        entry = sticker_map.get(name)
        if not entry:
            return ""
        if isinstance(entry, str):
            fp = _sticker_dir(config) / entry
            if fp.exists():
                stickers.append({"type": "file", "value": str(fp)})
        elif isinstance(entry, dict):
            if "file_id" in entry:
                stickers.append({"type": "file_id", "value": entry["file_id"]})
            elif "file" in entry:
                fp = _sticker_dir(config) / entry["file"]
                if fp.exists():
                    stickers.append({"type": "file", "value": str(fp)})
        return ""

    cleaned = re.sub(r"\[sticker:([^\]]+)\]", replacer, text).strip()
    return cleaned, stickers


# ------------------------------------------------------------------
# Email
# ------------------------------------------------------------------

def _email_send(
    smtp_host: str, smtp_port: int,
    from_addr: str, to_addr: str,
    subject: str, body: str,
    password: str = "",
) -> bool:
    """Send a plain-text email via SMTP with STARTTLS. Returns True on success."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            if password:
                s.login(from_addr, password)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[postman] Email send failed: {e}")
        return False


# ------------------------------------------------------------------
# Dispatch logic
# ------------------------------------------------------------------

def dispatch_file(path: Path, config: FiamConfig) -> bool:
    """Read a single outbox file, dispatch it, return True on success."""
    post = frontmatter.load(str(path))
    via = post.metadata.get("via", "telegram")
    body = post.content.strip()
    if not body:
        return False

    if via == "telegram":
        token = os.environ.get(config.tg_bot_token_env, "")
        chat_id = config.tg_chat_id
        if not token or not chat_id:
            print(f"[postman] TG not configured, skipping {path.name}")
            return False
        # Extract [sticker:xxx] tags before sending
        text, stickers = _extract_stickers(body, config)
        ok = True
        if text:
            ok = _tg_send(token, chat_id, text)
        for stk in stickers:
            if stk["type"] == "file_id":
                _tg_send_sticker(token, chat_id, stk["value"])
            elif stk["type"] == "file":
                _tg_send_photo(token, chat_id, Path(stk["value"]))
        return ok

    elif via == "email":
        subject = str(post.metadata.get("subject", f"From {config.ai_name}"))
        from_addr = config.email_from
        to_addr = config.email_to
        password = os.environ.get("FIAM_EMAIL_PASSWORD", "")
        if not from_addr or not to_addr or not config.email_smtp_host:
            print(f"[postman] Email not configured, skipping {path.name}")
            return False
        return _email_send(
            config.email_smtp_host, config.email_smtp_port,
            from_addr, to_addr, subject, body,
            password=password,
        )

    print(f"[postman] Unknown channel: {via}")
    return False


def sweep_outbox(config: FiamConfig) -> int:
    """Dispatch all pending outbox files. Returns count of sent messages."""
    outbox = config.outbox_dir
    sent_dir = config.outbox_sent_dir
    sent_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in sorted(outbox.glob("*.md")):
        if dispatch_file(f, config):
            # Move to sent/
            dest = sent_dir / f"{f.stem}_{int(time.time())}{f.suffix}"
            shutil.move(str(f), str(dest))
            print(f"[postman] ✓ {f.name} → {dest.name}")
            count += 1
        else:
            print(f"[postman] ✗ {f.name} (dispatch failed)")
    return count


# ------------------------------------------------------------------
# Daemon mode — run as background watcher
# ------------------------------------------------------------------

def run_postman_loop(config: FiamConfig, poll_seconds: int = 15) -> None:
    """Poll outbox/ every N seconds and dispatch new files."""
    print(f"[postman] Watching {config.outbox_dir} (poll={poll_seconds}s)")
    while True:
        try:
            sweep_outbox(config)
        except KeyboardInterrupt:
            print("[postman] Stopped.")
            break
        except Exception as e:
            print(f"[postman] Error: {e}")
        time.sleep(poll_seconds)


# ------------------------------------------------------------------
# Inbox JSONL helper — write messages for hook consumption
# ------------------------------------------------------------------

def _append_inbox_jsonl(config: FiamConfig, from_name: str, via: str, body: str) -> None:
    """Append a message to inbox.jsonl for the UserPromptSubmit hook.

    Format: one JSON object per line:
        {"from":"Zephyr","via":"telegram","body":"message text","ts":"..."}

    The hook (inject.sh) claims this file via atomic `mv` before reading.
    """
    import fcntl
    path = config.inbox_jsonl_path
    entry = json.dumps({
        "from": from_name,
        "via": via,
        "body": body.strip(),
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False)
    # Append with file locking to avoid partial writes
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(entry + "\n")
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ------------------------------------------------------------------
# IMAP inbox fetch — pull new emails into home/inbox/
# ------------------------------------------------------------------

def fetch_inbox(
    config: FiamConfig,
    *,
    imap_host: str = "imappro.zoho.com",
    imap_port: int = 993,
    max_fetch: int = 10,
) -> int:
    """Fetch unread emails via IMAP and save as Markdown in inbox/.

    Each email becomes one .md file with YAML frontmatter:
        ---
        from: sender@example.com
        subject: ...
        date: ISO timestamp
        via: email
        ---
        Body text

    Returns count of new messages saved.
    """
    user = config.email_from
    password = os.environ.get("FIAM_EMAIL_PASSWORD", "")
    if not user or not password:
        print("[postman] IMAP not configured (email_from or FIAM_EMAIL_PASSWORD missing)")
        return 0

    inbox_dir = config.inbox_dir
    inbox_dir.mkdir(parents=True, exist_ok=True)

    try:
        conn = imaplib.IMAP4_SSL(imap_host, imap_port)
        conn.login(user, password)
        conn.select("INBOX")

        # Search for UNSEEN messages
        status, data = conn.search(None, "UNSEEN")
        if status != "OK" or not data[0]:
            conn.logout()
            return 0

        msg_ids = data[0].split()[-max_fetch:]  # latest N
        count = 0

        for mid in msg_ids:
            status, msg_data = conn.fetch(mid, "(RFC822)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = email_mod.message_from_bytes(raw)

            sender = msg.get("From", "unknown")
            subject = msg.get("Subject", "(no subject)")
            date_str = msg.get("Date", "")

            # Extract plain text body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            body = payload.decode(charset, errors="replace")
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")

            # Write as Markdown
            ts = datetime.now().strftime("%m%d_%H%M%S")
            fname = f"email_{ts}_{count:02d}.md"
            md = (
                f"---\n"
                f"from: {sender}\n"
                f"subject: {subject}\n"
                f"date: {date_str}\n"
                f"via: email\n"
                f"---\n\n"
                f"{body.strip()}\n"
            )
            (inbox_dir / fname).write_text(md, encoding="utf-8")
            # Also write to inbox.jsonl for hook consumption
            _append_inbox_jsonl(config, sender, "email", body.strip())
            count += 1

        conn.logout()
        if count:
            print(f"[postman] Fetched {count} new email(s) → {inbox_dir}")
        return count

    except Exception as e:
        print(f"[postman] IMAP fetch failed: {e}")
        return 0


# ------------------------------------------------------------------
# Telegram inbound — poll for new messages via getUpdates
# ------------------------------------------------------------------

_tg_update_offset: int = 0  # module-level state for long-polling offset


def fetch_tg_inbox(config: FiamConfig, timeout: int = 0) -> int:
    """Poll Telegram Bot API for new messages and save to inbox/.

    Each message becomes one .md file with YAML frontmatter:
        ---
        from: username or first_name
        via: telegram
        date: ISO timestamp
        message_id: 12345
        ---
        Message text

    Returns count of new messages saved.
    """
    global _tg_update_offset

    token = os.environ.get(config.tg_bot_token_env, "")
    chat_id = config.tg_chat_id
    if not token or not chat_id:
        return 0

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": timeout, "allowed_updates": '["message"]'}
    if _tg_update_offset:
        params["offset"] = _tg_update_offset

    query = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(f"{url}?{query}", method="GET")

    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError):
        return 0

    if not data.get("ok") or not data.get("result"):
        return 0

    inbox_dir = config.inbox_dir
    inbox_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for update in data["result"]:
        _tg_update_offset = update["update_id"] + 1

        msg = update.get("message")
        if not msg:
            continue

        # Only accept messages from our chat
        msg_chat_id = str(msg.get("chat", {}).get("id", ""))
        if msg_chat_id != str(chat_id):
            continue

        # Extract content: text, sticker, or photo caption
        text = msg.get("text", "")
        sticker = msg.get("sticker")
        if sticker and not text:
            # Incoming sticker — record as [sticker:emoji] with file_id
            emoji = sticker.get("emoji", "")
            file_id = sticker.get("file_id", "")
            # Look up or describe the sticker
            idx = _load_sticker_index(config)
            match_name = None
            for name, entry in idx.items():
                if isinstance(entry, dict) and entry.get("file_id") == file_id:
                    match_name = name
                    break
            if match_name:
                text = f"[sticker:{match_name}]"
            else:
                text = f"[sticker:{emoji or 'unknown'}] (file_id: {file_id})"
        if not text:
            continue

        sender = msg.get("from", {})
        from_name = sender.get("username") or sender.get("first_name", "unknown")
        msg_id = msg.get("message_id", 0)
        msg_date = datetime.fromtimestamp(msg.get("date", 0)).strftime("%m-%d %H:%M")

        ts = datetime.now().strftime("%m%d_%H%M%S")
        fname = f"tg_{ts}_{count:02d}.md"
        md = (
            f"---\n"
            f"from: {from_name}\n"
            f"via: telegram\n"
            f"date: {msg_date}\n"
            f"message_id: {msg_id}\n"
            f"---\n\n"
            f"{text.strip()}\n"
        )
        (inbox_dir / fname).write_text(md, encoding="utf-8")
        # Also write to inbox.jsonl for hook consumption
        _append_inbox_jsonl(config, from_name, "telegram", text.strip())
        count += 1

    if count:
        print(f"[postman] Fetched {count} TG message(s) → {inbox_dir}")
    return count
