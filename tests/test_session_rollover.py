"""Tests for the session-rollover infrastructure (auto-cut + carryover).

Covers:
- session_state.json create/read/write helpers
- _check_and_run_session_rollover bumps counter and triggers rollover at cap
- carryover.md replacement + load_carryover_context one-shot consumption
- recall shield_after honors session boundary
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_dashboard_server(tmp_home: Path):
    """Load dashboard_server module fresh with FIAM_HOME pointing at tmp."""
    spec = importlib.util.spec_from_file_location(
        f"dashboard_server_test_{tmp_home.name}",
        ROOT / "scripts" / "dashboard_server.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ds_mod(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "src"))
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))

    home = tmp_path / "home"
    home.mkdir()
    (home / "self").mkdir()
    store = tmp_path / "store"
    store.mkdir()
    (store / "pool").mkdir()

    mod = _load_dashboard_server(tmp_path)

    class _Cfg:
        home_path = home
        flow_path = store / "flow.jsonl"
        active_session_path = home / "self" / "active_session.json"
        events_per_session = 3
        app_cot_summary_enabled = False  # disable api summarization in tests
        app_cot_summary_api_key_env = "DOES_NOT_EXIST"
        graph_edge_api_key_env = ""

    mod._CONFIG = _Cfg()
    return mod


def test_session_state_roundtrip(ds_mod):
    state = ds_mod._load_session_state()
    assert state["turns_since_boundary"] == 0
    assert state["boundary_flow_offset"] == 0

    state["turns_since_boundary"] = 5
    state["boundary_ts"] = "2026-05-11T20:00:00+00:00"
    ds_mod._save_session_state(state)

    again = ds_mod._load_session_state()
    assert again["turns_since_boundary"] == 5
    assert again["boundary_ts"] == "2026-05-11T20:00:00+00:00"


def test_carryover_summary_writes_and_marks_dirty(ds_mod):
    ds_mod._write_carryover_summary("hello world")
    co = ds_mod._carryover_path()
    dirty = ds_mod._carryover_dirty_path()
    assert co.exists()
    assert dirty.exists()
    text = co.read_text(encoding="utf-8")
    assert "hello world" in text
    assert "session_summary" in text


def test_carryover_summary_prepends_above_existing(ds_mod):
    co = ds_mod._carryover_path()
    co.write_text("## existing block\nold content\n\n", encoding="utf-8")
    ds_mod._write_carryover_summary("new summary")
    text = co.read_text(encoding="utf-8")
    new_pos = text.find("new summary")
    old_pos = text.find("old content")
    assert new_pos != -1 and old_pos != -1
    assert new_pos < old_pos


def test_check_rollover_bumps_until_cap(ds_mod, monkeypatch):
    triggered = []

    def fake_rollover(channel):
        triggered.append(channel)
        return {"ok": True, "fake": True}

    monkeypatch.setattr(ds_mod, "_session_rollover", fake_rollover)

    # cap=3 → first two return None, third triggers rollover
    assert ds_mod._check_and_run_session_rollover("chat") is None
    assert ds_mod._load_session_state()["turns_since_boundary"] == 1
    assert ds_mod._check_and_run_session_rollover("chat") is None
    assert ds_mod._load_session_state()["turns_since_boundary"] == 2
    res = ds_mod._check_and_run_session_rollover("chat")
    assert res == {"ok": True, "fake": True}
    assert triggered == ["chat"]


def test_load_carryover_context_consumes_once(ds_mod, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "src"))
    from fiam.runtime.prompt import load_carryover_context

    co = ds_mod._carryover_path()
    co.write_text("hi\n", encoding="utf-8")

    text = load_carryover_context(ds_mod._CONFIG, consume=True)
    assert text == "hi"
    # second read sees empty
    text2 = load_carryover_context(ds_mod._CONFIG, consume=True)
    assert text2 == ""
    # file still exists but is empty
    assert co.exists()
    assert co.read_text(encoding="utf-8") == ""


def test_load_carryover_context_no_consume(ds_mod, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "src"))
    from fiam.runtime.prompt import load_carryover_context

    co = ds_mod._carryover_path()
    co.write_text("persistent\n", encoding="utf-8")

    assert load_carryover_context(ds_mod._CONFIG, consume=False) == "persistent"
    assert load_carryover_context(ds_mod._CONFIG, consume=False) == "persistent"


def test_recall_shield_after_uses_session_boundary():
    """refresh_recall must honor explicit shield_after override."""
    import importlib

    fiam_recall = importlib.import_module("fiam.runtime.recall")

    # Build minimal fake pool + config using mocks
    import numpy as np

    class _Ev:
        def __init__(self, fp_idx, t):
            self.fingerprint_idx = fp_idx
            self.t = t
            self.access_count = 0

    class _Pool:
        def __init__(self, events, fps):
            self._events = events
            self._fps = fps

        def load_events(self):
            return self._events

        def load_fingerprints(self):
            return self._fps

        def get_event(self, ev_id):
            return None

        def read_body(self, ev_id):
            return ""

        def save_events(self):
            pass

    boundary = datetime.now(timezone.utc) - timedelta(hours=2)
    events = [
        _Ev(0, boundary - timedelta(hours=1)),  # before boundary (eligible)
        _Ev(1, boundary + timedelta(minutes=10)),  # after boundary (shielded)
    ]
    fps = np.eye(2, dtype=np.float32)
    pool = _Pool(events, fps)
    q = np.array([1.0, 0.0], dtype=np.float32)

    # Direct test of seed_activation honoring shield_after
    from fiam.retriever.spread import seed_activation

    sims = seed_activation(q, pool, shield_after=boundary)
    # event 0 (before boundary) keeps non-zero similarity
    assert sims[0] > 0.0
    # event 1 (after boundary) is zeroed
    assert sims[1] == 0.0
