from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fiam.config import FiamConfig
from fiam.runtime.prompt import append_transcript_messages, load_transcript_messages
from fiam.store.beat import Beat, append_beat, read_beats
from fiam.store.features import beat_key
from fiam.store.objects import ObjectStore


class EventStoreTest(unittest.TestCase):
    def test_append_is_idempotent_and_reads_from_sqlite(self) -> None:
        with TemporaryDirectory() as tmp:
            flow = Path(tmp) / "store" / "flow.jsonl"
            beat = Beat(
                t=datetime(2026, 5, 12, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="hello",
                meta={"message_id": "m1"},
                surface="favilla.chat",
            )

            first = append_beat(flow, beat)
            second = append_beat(flow, beat)

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(len(read_beats(flow)), 1)
            self.assertEqual(read_beats(flow)[0].surface, "favilla.chat")
            self.assertFalse(flow.exists())

    def test_large_content_uses_object_store(self) -> None:
        with TemporaryDirectory() as tmp:
            flow = Path(tmp) / "store" / "flow.jsonl"
            text = "x" * 9000
            append_beat(flow, Beat(
                t=datetime(2026, 5, 12, tzinfo=timezone.utc),
                actor="ai",
                channel="chat",
                kind="message",
                content=text,
                runtime="cc",
            ))

            beats = read_beats(flow)
            self.assertEqual(beats[0].content, text)
            self.assertTrue((flow.parent / "objects").is_dir())
            self.assertTrue(list((flow.parent / "objects").glob("*/*.txt")))

    def test_object_store_bytes_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ObjectStore(Path(tmp) / "objects")

            digest = store.put_bytes(b"\x00image-bytes", suffix="")

            self.assertEqual(store.get_bytes(digest, suffix=""), b"\x00image-bytes")
            self.assertEqual(len(digest), 64)

    def test_transcript_messages_read_store_transcripts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local")
            config.ensure_dirs()
            append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 12, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="NEW_EVENT",
            ))
            append_transcript_messages(config, "chat", [{"role": "user", "content": "SHARED_TRANSCRIPT"}])

            messages = load_transcript_messages(config, "chat")

            self.assertEqual(messages, [{"role": "user", "content": "SHARED_TRANSCRIPT"}])

    def test_transcript_messages_hide_control_markers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local")
            config.ensure_dirs()
            append_transcript_messages(config, "chat", [
                {"role": "assistant", "content": 'shown <cot>private</cot> <todo at="2026-05-12 20:00">x</todo>'},
                {"role": "assistant", "content": "<hold>retry</hold>"},
            ])

            messages = load_transcript_messages(config, "chat")

            self.assertEqual(messages, [{"role": "assistant", "content": "shown"}])

    def test_transcript_messages_drop_user_image_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local")
            config.ensure_dirs()
            append_transcript_messages(config, "chat", [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this\nobj:abc"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,SECRET"}},
                ],
            }])

            raw = (config.store_dir / "transcripts" / "chat.jsonl").read_text(encoding="utf-8")
            messages = load_transcript_messages(config, "chat")

            self.assertNotIn("data:image", raw)
            self.assertEqual(messages, [{"role": "user", "content": "describe this\nobj:abc"}])

    def test_feature_key_prefers_event_id(self) -> None:
        beat = Beat(
            t=datetime(2026, 5, 12, tzinfo=timezone.utc),
            actor="user",
            channel="chat",
            kind="message",
            content="hello",
            meta={"event_id": "ev_123"},
        )
        self.assertEqual(beat_key(beat), "event:ev_123")

    def test_schema_indexes_name_session_and_embedding_state(self) -> None:
        with TemporaryDirectory() as tmp:
            flow = Path(tmp) / "store" / "flow.jsonl"
            event_id = append_beat(flow, Beat(
                t=datetime(2026, 5, 12, tzinfo=timezone.utc),
                actor="ai",
                channel="chat",
                kind="think",
                content="raw thinking",
                runtime="cc",
                meta={"source": "official", "session_id": "s1"},
            ))
            from fiam.store.events import EventStore, db_path_for_flow
            EventStore(db_path_for_flow(flow)).mark_embedded(
                event_id or "",
                model_id="bge-test",
                embedded_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
            )

            conn = sqlite3.connect(db_path_for_flow(flow))
            try:
                row = conn.execute(
                    "SELECT session_id, name, embed_model, embedded_at FROM events WHERE id = ?",
                    (event_id,),
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row[0], "s1")
            self.assertEqual(row[1], "official")
            self.assertEqual(row[2], "bge-test")
            self.assertTrue(row[3].startswith("2026-05-12"))

    def test_schema_persists_turn_dispatch_and_object_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            flow = Path(tmp) / "store" / "flow.jsonl"
            event_id = append_beat(flow, Beat(
                t=datetime(2026, 5, 12, tzinfo=timezone.utc),
                actor="ai",
                channel="email",
                kind="message",
                content="hello",
                meta={
                    "turn_id": "turn_1",
                    "request_id": "req_1",
                    "session_id": "sess_1",
                    "dispatch_target": "email",
                    "dispatch_id": "disp_1",
                    "dispatch_recipient": "Zephyr",
                    "dispatch_status": "accepted",
                    "dispatch_attempts": 1,
                    "dispatch_last_error": "",
                    "object_mime": "text/plain",
                    "object_name": "note.txt",
                    "object_size": 12,
                    "surface": "favilla.chat",
                },
                surface="favilla.chat",
            ))

            from fiam.store.events import db_path_for_flow
            conn = sqlite3.connect(db_path_for_flow(flow))
            try:
                row = conn.execute(
                    "SELECT turn_id, request_id, surface, dispatch_id, dispatch_recipient, object_mime, object_size FROM events WHERE id = ?",
                    (event_id,),
                ).fetchone()
                object_index = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_events_object_hash'",
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(row, ("turn_1", "req_1", "favilla.chat", "disp_1", "Zephyr", "text/plain", 12))
            self.assertEqual(object_index, ("idx_events_object_hash",))


if __name__ == "__main__":
    unittest.main()
