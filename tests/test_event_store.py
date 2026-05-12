from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fiam.config import FiamConfig
from fiam.runtime.prompt import load_recent_conversation_context
from fiam.store.beat import Beat, append_beat, read_beats
from fiam.store.features import beat_key


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
            )

            first = append_beat(flow, beat)
            second = append_beat(flow, beat)

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(len(read_beats(flow)), 1)
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

    def test_recent_context_reads_event_store_not_transcript(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = FiamConfig(home_path=root / "home", code_path=root, embedding_backend="local")
            config.ensure_dirs()
            (config.home_path / "transcript").mkdir(parents=True)
            (config.home_path / "transcript" / "chat.jsonl").write_text(
                '{"role":"user","raw_text":"OLD_TRANSCRIPT"}\n',
                encoding="utf-8",
            )
            append_beat(config.flow_path, Beat(
                t=datetime(2026, 5, 12, tzinfo=timezone.utc),
                actor="user",
                channel="chat",
                kind="message",
                content="NEW_EVENT",
            ))

            context = load_recent_conversation_context(config, "chat")

            self.assertIn("NEW_EVENT", context)
            self.assertNotIn("OLD_TRANSCRIPT", context)

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


if __name__ == "__main__":
    unittest.main()
