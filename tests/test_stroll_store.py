from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SCRIPTS, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fiam.config import FiamConfig  # noqa: E402
from fiam_lib.stroll_store import (  # noqa: E402
    add_spatial_record,
    apply_spatial_record_markers,
    apply_stroll_action_markers,
    build_context_block,
    cell_id,
    list_spatial_records,
    neighbor_cell_ids,
    record_action_result,
)


class StrollStoreTest(unittest.TestCase):
    def test_cell_neighbors_include_self(self) -> None:
        cell = cell_id(121.5, 31.2)

        self.assertIn(cell, neighbor_cell_ids(cell))
        self.assertEqual(len(neighbor_cell_ids(cell)), 9)

    def test_nearby_records_filter_by_true_distance(self) -> None:
        with TemporaryDirectory() as tmp:
            config = FiamConfig(home_path=Path(tmp) / "home", code_path=Path(tmp) / "code")
            current = {"lng": 121.5, "lat": 31.2}
            near = add_spatial_record(config, {"kind": "note", "origin": "user", "lng": 121.5001, "lat": 31.2001, "text": "near note"})
            add_spatial_record(config, {"kind": "note", "origin": "ai", "lng": 121.5, "lat": 31.201, "text": "far note"})

            result = list_spatial_records(config, current=current, radius_m=50)

        self.assertTrue(result["ok"])
        self.assertEqual([row["id"] for row in result["records"]], [near["id"]])
        self.assertLessEqual(result["records"][0]["distanceM"], 50)
        self.assertIsInstance(result["records"][0]["bearingDeg"], float)
        self.assertTrue(result["contextVersion"])

    def test_context_block_includes_current_cell_and_records(self) -> None:
        with TemporaryDirectory() as tmp:
            config = FiamConfig(home_path=Path(tmp) / "home", code_path=Path(tmp) / "code")
            add_spatial_record(config, {"kind": "marker", "origin": "ai", "lng": 121.5001, "lat": 31.2001, "text": "turn left"})
            block, context = build_context_block(config, {"current": {"lng": 121.5, "lat": 31.2}, "placeKind": "road"})

        self.assertIn("[stroll_context]", block)
        self.assertIn("nearby_records<=50m", block)
        self.assertEqual(context["placeKind"], "road")
        self.assertTrue(context["cellId"])
        self.assertEqual(len(context["spatialRecords"]), 1)

    def test_ai_marker_uses_current_point_and_is_hidden(self) -> None:
        with TemporaryDirectory() as tmp:
            config = FiamConfig(home_path=Path(tmp) / "home", code_path=Path(tmp) / "code")
            cleaned, records = apply_spatial_record_markers(
                config,
                'visible reply <stroll_record kind="marker" text="north gate" placeKind="road" />',
                {"current": {"lng": 121.5, "lat": 31.2}, "placeKind": "road"},
            )
            nearby = list_spatial_records(config, current={"lng": 121.5, "lat": 31.2}, radius_m=50)

        self.assertEqual(cleaned, "visible reply")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["text"], "north gate")
        self.assertEqual(records[0]["origin"], "ai")
        self.assertEqual(records[0]["placeKind"], "road")
        self.assertEqual([row["id"] for row in nearby["records"]], [records[0]["id"]])

    def test_ai_action_marker_queues_client_action_and_is_hidden(self) -> None:
        with TemporaryDirectory() as tmp:
            config = FiamConfig(home_path=Path(tmp) / "home", code_path=Path(tmp) / "code")
            cleaned, actions = apply_stroll_action_markers(
                config,
                'visible <stroll_action type="view_camera" reason="look around" /><stroll_action type="capture_photo" reason="take still" /><stroll_action type="set_limen_screen" text="hello" emoji="spark" /><stroll_action type="refresh_nearby" reason="reload" />',
                {"current": {"lng": 121.5, "lat": 31.2}, "placeKind": "road"},
            )

        self.assertEqual(cleaned, "visible")
        self.assertEqual([action["type"] for action in actions], ["view_camera", "capture_photo", "set_limen_screen", "refresh_nearby"])
        self.assertEqual(actions[0]["status"], "queued")
        self.assertEqual(actions[0]["payload"]["reason"], "look around")
        self.assertEqual(actions[1]["payload"]["reason"], "take still")
        self.assertEqual(actions[2]["payload"]["text"], "hello")
        self.assertEqual(actions[3]["payload"]["reason"], "reload")

    def test_action_result_is_recorded(self) -> None:
        with TemporaryDirectory() as tmp:
            config = FiamConfig(home_path=Path(tmp) / "home", code_path=Path(tmp) / "code")
            record = record_action_result(config, {"actionId": "a1", "action": "camera.capture", "status": "ok"})

        self.assertEqual(record["id"], "a1")
        self.assertEqual(record["action"], "camera.capture")


if __name__ == "__main__":
    unittest.main()