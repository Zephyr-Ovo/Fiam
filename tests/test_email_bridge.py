from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (str(SCRIPTS), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

spec = importlib.util.spec_from_file_location("bridge_email", SCRIPTS / "bridges" / "bridge_email.py")
assert spec and spec.loader
bridge_email = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge_email)

from fiam.config import FiamConfig  # noqa: E402
from fiam.store.beat import read_beats  # noqa: E402
from fiam.store.objects import ObjectStore  # noqa: E402
from fiam_lib.postman import _email_send  # noqa: E402


class EmailBridgeAttachmentTest(unittest.TestCase):
    def test_dispatch_attachments_accept_object_hash_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            digest = ObjectStore(config.object_dir).put_bytes(b"hello attachment", suffix="")

            accepted = bridge_email._dispatch_attachments({
                "attachments": [{"object_hash": digest, "name": "note.txt", "mime": "text/plain"}],
            }, config)
            self.assertEqual(accepted[0]["object_hash"], digest)
            self.assertTrue(Path(accepted[0]["path"]).is_file())
            with self.assertRaises(ValueError):
                bridge_email._dispatch_attachments({
                    "attachments": [{"path": accepted[0]["path"], "name": "note.txt", "mime": "text/plain"}],
                }, config)

    def test_on_dispatch_passes_object_attachments_to_smtp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            config.email_from = "ai@example.com"
            config.email_to = "zephyr@example.com"
            config.email_smtp_host = "smtp.example.com"
            digest = ObjectStore(config.object_dir).put_bytes(b"hello attachment", suffix="")
            bridge_email._on_dispatch.config = config
            with patch.object(bridge_email, "is_dispatch_enabled", return_value=True), patch.object(bridge_email, "_email_send", return_value=True) as send:
                bridge_email._on_dispatch("email", {
                    "text": "",
                    "recipient": "zephyr@example.com",
                    "dispatch_id": "disp_email_ok",
                    "turn_id": "turn_email_ok",
                    "attachments": [{"object_hash": digest, "name": "note.txt", "mime": "text/plain"}],
                })

            self.assertEqual(send.call_args.kwargs["attachments"][0]["object_hash"], digest)
            self.assertEqual(send.call_args.args[4], "(see attached file)")
            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            self.assertEqual((dispatch_beats[-1].meta or {}).get("dispatch_status"), "delivered")
            self.assertEqual((dispatch_beats[-1].meta or {}).get("dispatch_id"), "disp_email_ok")
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertIn("dispatch.delivered", [row["phase"] for row in trace_rows])

    def test_on_dispatch_does_not_send_when_attachment_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            config.email_from = "ai@example.com"
            config.email_to = "zephyr@example.com"
            config.email_smtp_host = "smtp.example.com"
            bridge_email._on_dispatch.config = config
            with patch.object(bridge_email, "is_dispatch_enabled", return_value=True), patch.object(bridge_email, "_email_send", return_value=True) as send:
                bridge_email._on_dispatch("email", {
                    "text": "body",
                    "recipient": "zephyr@example.com",
                    "dispatch_id": "disp_email_missing",
                    "attachments": [{"object_hash": "f" * 64, "name": "missing.bin"}],
                })

            send.assert_not_called()
            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            self.assertEqual((dispatch_beats[-1].meta or {}).get("dispatch_status"), "failed")
            self.assertIn("not found", (dispatch_beats[-1].meta or {}).get("dispatch_last_error"))
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertIn("dispatch.failed", [row["phase"] for row in trace_rows])

    def test_on_dispatch_empty_payload_records_failed_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root / "code")
            config.ensure_dirs()
            bridge_email._on_dispatch.config = config
            with patch.object(bridge_email, "is_dispatch_enabled", return_value=True), patch.object(bridge_email, "_email_send", return_value=True) as send:
                bridge_email._on_dispatch("email", {
                    "text": "",
                    "recipient": "zephyr@example.com",
                    "dispatch_id": "disp_email_empty",
                    "turn_id": "turn_email_empty",
                })

            send.assert_not_called()
            dispatch_beats = [beat for beat in read_beats(config.flow_path) if beat.kind == "dispatch"]
            self.assertEqual((dispatch_beats[-1].meta or {}).get("dispatch_status"), "failed")
            self.assertIn("empty dispatch payload", (dispatch_beats[-1].meta or {}).get("dispatch_last_error"))
            self.assertEqual((dispatch_beats[-1].meta or {}).get("dispatch_id"), "disp_email_empty")
            trace_rows = [json.loads(line) for line in (config.store_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertIn("dispatch.failed", [row["phase"] for row in trace_rows])

    def test_email_send_builds_mime_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            attachment_path = Path(tmp) / "note.txt"
            attachment_path.write_text("hello attachment", encoding="utf-8")
            smtp = Mock()
            smtp.__enter__ = Mock(return_value=smtp)
            smtp.__exit__ = Mock(return_value=False)
            with patch("smtplib.SMTP", return_value=smtp):
                ok = _email_send(
                    "smtp.example.com",
                    587,
                    "ai@example.com",
                    "zephyr@example.com",
                    "subject",
                    "body",
                    attachments=[{"path": str(attachment_path), "name": "note.txt", "mime": "text/plain"}],
                )

            self.assertTrue(ok)
            message = smtp.send_message.call_args.args[0]
            self.assertTrue(message.is_multipart())
            filenames = [part.get_filename() for part in message.iter_attachments()]
            self.assertEqual(filenames, ["note.txt"])


if __name__ == "__main__":
    unittest.main()