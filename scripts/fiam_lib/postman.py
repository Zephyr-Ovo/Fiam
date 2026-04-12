"""
Outbox dispatcher — delivers AI's messages via Telegram or Email.

The AI writes Markdown files to home/outbox/ with YAML frontmatter:

    ---
    to: zephyr
    via: telegram        # telegram | email
    priority: normal     # normal | urgent
    ---

    Message body here...

This module watches outbox/ and dispatches each file, then moves it
to outbox/sent/. It never touches the AI's conversation or CC session.
"""

from __future__ import annotations

import json
import os
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
        return _tg_send(token, chat_id, body)

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
