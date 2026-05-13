"""
Outbox dispatcher — delivers AI's messages via Email.

The AI writes Markdown files to home/outbox/ with YAML frontmatter:

    ---
    to: zephyr
    via: email
    priority: normal     # normal | urgent
    ---

    Message body here...

This module watches outbox/ and dispatches each file, then moves it
to outbox/sent/. It never touches the AI's conversation or CC session.
"""

from __future__ import annotations

import json
import os
import imaplib
import email as email_mod
from email.utils import parsedate_to_datetime
import shutil
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import frontmatter

from fiam.config import FiamConfig
from fiam.turn import MarkerInterpreter


# ------------------------------------------------------------------
# Contacts
# ------------------------------------------------------------------

def _resolve_contact(name: str, config: FiamConfig) -> str:
    """Look up a named contact's email in self/contacts.json. Returns email or ''."""
    if not name:
        return ""
    contacts_path = config.self_dir / "contacts.json"
    if not contacts_path.exists():
        return ""
    try:
        contacts = json.loads(contacts_path.read_text(encoding="utf-8"))
        key = name.lower()
        for entry in contacts:
            if entry.get("name", "").lower() == key or entry.get("alias", "").lower() == key:
                return entry.get("email", "")
    except Exception:
        pass
    return ""


def _email_send(
    smtp_host: str, smtp_port: int,
    from_addr: str, to_addr: str,
    subject: str, body: str,
    password: str = "",
    attachments: list[dict] | None = None,
) -> bool:
    """Send a plain-text email via SMTP. Uses SSL for port 465, STARTTLS otherwise."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body or "")
    for att in attachments or []:
        path = Path(str(att.get("path") or ""))
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        mime = str(att.get("mime") or "application/octet-stream").strip().lower()
        maintype, _, subtype = mime.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        filename = Path(str(att.get("name") or path.name)).name or path.name
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
                if password:
                    s.login(from_addr, password)
                s.send_message(msg)
        else:
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
    via = post.metadata.get("via", "email")
    body = post.content.strip()
    if not body:
        return False

    body = MarkerInterpreter().interpret(body).visible_reply
    if not body:
        return True  # Only had control markers, nothing to send

    if via == "email":
        subject = str(post.metadata.get("subject", "From ai"))
        from_addr = config.email_from
        to_field = str(post.metadata.get("to", "")).strip()
        # Resolve recipient: bare email address, named contact, or config default
        if "@" in to_field:
            to_addr = to_field
        else:
            to_addr = _resolve_contact(to_field, config) or config.email_to
        password = os.environ.get("FIAM_EMAIL_PASSWORD", "")
        if not from_addr or not to_addr or not config.email_smtp_host:
            print(f"[postman] Email not configured, skipping {path.name}")
            return False
        print(f"[postman] email → {to_addr}")
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
# IMAP inbox fetch — pull new emails into home/inbox/
# ------------------------------------------------------------------

def fetch_inbox(
    config: FiamConfig,
    *,
    imap_host: str = "imappro.zoho.com",
    imap_port: int = 993,
    max_fetch: int = 10,
) -> list[dict]:
    """Fetch unread emails via IMAP and save as Markdown in inbox/.

    Returns list of message dicts: [{from_name, source, text}].
    """
    user = config.email_from
    password = os.environ.get("FIAM_EMAIL_PASSWORD", "")
    if not user or not password:
        print("[postman] IMAP not configured (email_from or FIAM_EMAIL_PASSWORD missing)")
        return []

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
            return []

        msg_ids = data[0].split()[-max_fetch:]  # latest N
        messages = []

        for idx, mid in enumerate(msg_ids):
            status, msg_data = conn.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            # Mark as seen so we don't fetch it again
            conn.store(mid, "+FLAGS", "\\Seen")

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
            fname = f"email_{ts}_{idx:02d}.md"
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
            try:
                t_msg = parsedate_to_datetime(date_str) if date_str else datetime.now()
            except (TypeError, ValueError):
                t_msg = datetime.now()
            messages.append({"from_name": sender, "source": "email", "text": body.strip(), "t": t_msg})

        conn.logout()
        if messages:
            print(f"[postman] Fetched {len(messages)} new email(s) → {inbox_dir}")
        return messages

    except Exception as e:
        print(f"[postman] IMAP fetch failed: {e}")
        return []
