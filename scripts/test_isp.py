#!/usr/bin/env python3
"""
ISP Deployment Verification — run from ISP.
Usage: cd /root/fiam-code && source .venv/bin/activate && python scripts/test_isp.py
"""
import os, sys

os.chdir("/root/fiam-code")
sys.path.insert(0, "/root/fiam-code/src")
sys.path.insert(0, "/root/fiam-code/scripts")

from pathlib import Path

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

results = []

def test(name, fn):
    try:
        fn()
        print(f"  [{GREEN}PASS{RESET}] {name}")
        results.append((name, True, None))
    except Exception as e:
        print(f"  [{RED}FAIL{RESET}] {name}: {e}")
        results.append((name, False, str(e)))

# ═══════════════════════════════════════
# Test A: Config & Store
# ═══════════════════════════════════════
print("\n=== A: Config & Store ===")

def test_config():
    from fiam.config import FiamConfig
    cfg = FiamConfig.from_toml(Path("fiam.toml"), Path("."))
    assert cfg.ai_name == "Fiet", f"ai_name={cfg.ai_name}"
    assert cfg.home_path == Path("/root/fiet-home"), f"home={cfg.home_path}"

def test_home_dirs():
    for d in ["self/journal", "outbox/sent", "inbox", "world", "zephyr"]:
        p = Path("/root/fiet-home") / d
        assert p.is_dir(), f"missing: {p}"

def test_store():
    events = list(Path("store/events").glob("*.md"))
    embeds = list(Path("store/embeddings").glob("*.npy"))
    assert len(events) == 81, f"events={len(events)}"
    assert len(embeds) == 81, f"embeds={len(embeds)}"
    print(f"    → {len(events)} events, {len(embeds)} embeddings")

def test_env_vars():
    for var in ["FIAM_TG_BOT_TOKEN", "FIAM_EMAIL_PASSWORD"]:
        assert os.environ.get(var), f"{var} not set"

test("FiamConfig loads", test_config)
test("Home directories", test_home_dirs)
test("Store: 81 events + 81 embeddings", test_store)
test("Env vars (TG + Email)", test_env_vars)

# ═══════════════════════════════════════
# Test B: Graph Retrieval
# ═══════════════════════════════════════
print("\n=== B: Graph Retrieval ===")

def test_load_events():
    from fiam.config import FiamConfig
    from fiam.store.home import HomeStore
    cfg = FiamConfig.from_toml(Path("fiam.toml"), Path("."))
    store = HomeStore(cfg)
    events = store.all_events()
    assert len(events) == 81

def test_graph_build():
    from fiam.config import FiamConfig
    from fiam.store.home import HomeStore
    from fiam.retriever.graph import MemoryGraph
    cfg = FiamConfig.from_toml(Path("fiam.toml"), Path("."))
    store = HomeStore(cfg)
    events = store.all_events()
    g = MemoryGraph()
    g.build(events)
    assert g.G.number_of_nodes() == 81
    assert g.G.number_of_edges() > 0
    print(f"    → {g.G.number_of_nodes()} nodes, {g.G.number_of_edges()} edges")

def test_spread():
    from fiam.config import FiamConfig
    from fiam.store.home import HomeStore
    from fiam.retriever.graph import MemoryGraph
    cfg = FiamConfig.from_toml(Path("fiam.toml"), Path("."))
    store = HomeStore(cfg)
    events = store.all_events()
    g = MemoryGraph()
    g.build(events)
    seed_ids = [events[0].event_id]
    seed_scores = [1.0]
    activation = g.spread(seed_ids, seed_scores)
    assert len(activation) > 1, "Spreading should activate more than seed"
    top3 = sorted(activation.items(), key=lambda x: -x[1])[:3]
    print(f"    → Spread from {events[0].event_id}: {top3}")

test("Load events via HomeStore", test_load_events)
test("Build MemoryGraph (NetworkX)", test_graph_build)
test("Spreading activation (2-hop)", test_spread)

# ═══════════════════════════════════════
# Test C: TG Notification
# ═══════════════════════════════════════
print("\n=== C: Telegram ===")

def test_tg():
    from fiam_lib.postman import _tg_send
    token = os.environ["FIAM_TG_BOT_TOKEN"]
    ok = _tg_send(token=token, chat_id="8629595965",
                  text="✅ ISP verification complete — Fiet is operational.")
    assert ok, "TG send returned False"

test("Send TG message", test_tg)

# ═══════════════════════════════════════
# Test D: Email
# ═══════════════════════════════════════
print("\n=== D: Email (Zoho) ===")

def test_email():
    from fiam_lib.postman import _email_send
    pw = os.environ["FIAM_EMAIL_PASSWORD"]
    ok = _email_send(
        smtp_host="smtppro.zoho.com", smtp_port=587,
        from_addr="fiet@fiet.cc", to_addr="fiet@fiet.cc",
        subject="ISP verification complete",
        body="All tests passed. Graph memory is live on ISP.",
        password=pw,
    )
    assert ok, "Email send returned False"

test("Send email (Zoho SMTP)", test_email)

# ═══════════════════════════════════════
# Test E: SSH ISP → DO
# ═══════════════════════════════════════
print("\n=== E: ISP → DO Connectivity ===")

def test_isp_to_do():
    import subprocess
    r = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
         "root@209.38.69.231", "echo DO_OK"],
        capture_output=True, text=True, timeout=15,
    )
    assert "DO_OK" in r.stdout, f"stdout={r.stdout}, stderr={r.stderr}"

test("SSH to DO from ISP", test_isp_to_do)

# ═══════════════════════════════════════
# Test F: Pipeline Imports
# ═══════════════════════════════════════
print("\n=== F: Pipeline Imports ===")

def test_pipeline_imports():
    from fiam.retriever import joint as jr
    from fiam.synthesizer.stance import StanceSynthesizer
    from fiam.personality.reader import read_personality
    from fiam.retriever.semantic_link import link_semantic
    from fiam.retriever.temporal import link_new_events
    # All good

test("All pipeline modules importable", test_pipeline_imports)

# ═══════════════════════════════════════
# Summary
# ═══════════════════════════════════════
print("\n" + "=" * 50)
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"Results: {passed}/{total} passed")
for name, ok, err in results:
    status = "✓" if ok else "✗"
    line = f"  {status} {name}"
    if err:
        line += f" — {err}"
    print(line)
print("=" * 50)

if passed < total:
    sys.exit(1)
