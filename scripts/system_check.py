#!/usr/bin/env python3
"""fiam system comprehensive check — runs all component tests on ISP."""
import json, os, sys, time, socket, subprocess
from pathlib import Path
from datetime import datetime, timezone

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "src"))
sys.path.insert(0, str(_root / "scripts"))
os.chdir(_root)

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
results = []

def log(tag, name, detail=""):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {tag:4s}  {name}"
    if detail:
        line += f"  -- {detail}"
    print(line)
    results.append({"tag": tag, "name": name, "detail": detail})

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# --------------- Config ---------------
from fiam_lib.core import _build_config
import argparse
sys.argv = ["fiam"]
args = argparse.Namespace(config=None, home=None, code=None)
config = _build_config(args)

HOME = Path(config.home_path)
CODE = Path(config.code_path)
STORE = CODE / "store"

# ================================================================
section("1. RECALL MECHANISM")
# ================================================================

n_events = len(list((STORE / "events").glob("*.json")))
log(PASS if n_events > 0 else FAIL, "Events in store", f"{n_events} events")

n_emb = len(list((STORE / "embeddings").glob("*.npy")))
if n_emb == n_events:
    log(PASS, "Embeddings match events", f"{n_emb}/{n_events}")
elif n_emb > 0:
    log(WARN, "Embeddings count mismatch", f"{n_emb} emb vs {n_events} events")
else:
    log(FAIL, "No embeddings", "run reindex")

graph_file = STORE / "graph.jsonl"
if graph_file.exists():
    n_edges = sum(1 for _ in open(graph_file))
    log(PASS, "Graph edges", f"{n_edges} edges")
else:
    log(FAIL, "Graph file missing")

recall = HOME / "recall.md"
if recall.exists():
    age_min = (time.time() - recall.stat().st_mtime) / 60
    lines = recall.read_text().strip().split("\n")
    log(PASS, "recall.md present", f"{len(lines)} lines, {age_min:.0f}min old")
else:
    log(FAIL, "recall.md missing")

try:
    from fiam.retriever import joint as joint_retriever
    from fiam.store.home import HomeStore
    store_obj = HomeStore(config)
    events = joint_retriever.search("", store_obj, config)
    log(PASS, "Joint retriever", f"returned {len(events)} events")
except Exception as e:
    log(FAIL, "Joint retriever", str(e))

# ================================================================
section("2. HOOK INJECTION")
# ================================================================

hooks_dir = HOME / ".claude" / "hooks"
for hook_name in ["inject.sh", "outbox.sh", "boot.sh", "compact.sh"]:
    hook_path = hooks_dir / hook_name
    if hook_path.exists():
        if os.access(hook_path, os.X_OK):
            log(PASS, f"Hook {hook_name}", "present + executable")
        else:
            log(WARN, f"Hook {hook_name}", "present but NOT executable")
    else:
        log(FAIL, f"Hook {hook_name}", "MISSING")

inject = hooks_dir / "inject.sh"
if inject.exists():
    ic = inject.read_text()
    log(PASS if "recall.md" in ic else WARN, "inject.sh reads recall.md")
    log(PASS if "inbox.jsonl" in ic else WARN, "inject.sh reads inbox.jsonl")

settings = HOME / ".claude" / "settings.local.json"
if settings.exists():
    sj = json.loads(settings.read_text())
    hooks = sj.get("hooks", {})
    log(PASS, "settings.local.json", f"hook types: {list(hooks.keys())}")
else:
    log(FAIL, "settings.local.json missing")

# ================================================================
section("3. DRIFT DETECTION")
# ================================================================

try:
    from fiam_lib.daemon import _update_recall_if_drifted
    log(PASS, "Drift detection function", "importable")
except ImportError as e:
    log(FAIL, "Drift detection function", str(e))

# ================================================================
section("4. GIT DIFF AWARENESS")
# ================================================================

has_git = (HOME / ".git").exists() or (HOME / ".gitattributes").exists()
log(PASS if has_git else FAIL, "fiet-home is git repo")

try:
    from fiam.injector.home_diff import detect_uncommitted
    diff = detect_uncommitted(config)
    if diff is not None:
        log(PASS, "Git diff detection", f"{len(diff)} chars of diff")
    else:
        log(PASS, "Git diff detection", "no uncommitted changes")
except Exception as e:
    log(FAIL, "Git diff detection", str(e))

log(PASS if config.git_enabled else WARN, "Git features enabled", str(config.git_enabled))

# ================================================================
section("5. EVENT SEGMENTATION")
# ================================================================

try:
    from fiam.extractor import event as event_extractor
    log(PASS, "Event extractor importable")
except Exception as e:
    log(FAIL, "Event extractor import", str(e))

try:
    from fiam.classifier.emotion import get_classifier
    clf = get_classifier(config)
    log(PASS, "Emotion classifier loaded", type(clf).__name__)
except Exception as e:
    log(FAIL, "Emotion classifier", str(e))

# ================================================================
section("6. EMAIL READ/WRITE")
# ================================================================

smtp_host = config.email_smtp_host
smtp_port = config.email_smtp_port
email_from = config.email_from
log(PASS if smtp_host else FAIL, "SMTP host", smtp_host)
log(PASS if smtp_port else FAIL, "SMTP port", str(smtp_port))
log(PASS if email_from else FAIL, "Email from", email_from)

email_pw = os.environ.get("FIAM_EMAIL_PASSWORD", "")
log(PASS if email_pw else FAIL, "Email password env", f"set ({len(email_pw)} chars)" if email_pw else "not set")

if email_from and email_pw:
    try:
        import imaplib
        conn = imaplib.IMAP4_SSL("imappro.zoho.com", 993)
        conn.login(email_from, email_pw)
        status, data = conn.select("INBOX", readonly=True)
        msg_count = data[0].decode()
        conn.logout()
        log(PASS, "IMAP connection", f"inbox has {msg_count} messages")
    except Exception as e:
        log(FAIL, "IMAP connection", str(e))

    try:
        import smtplib
        if smtp_port == 465:
            s = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
        else:
            s = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            s.starttls()
        s.login(email_from, email_pw)
        s.quit()
        log(PASS, "SMTP connection", f"{smtp_host}:{smtp_port}")
    except Exception as e:
        log(FAIL, "SMTP connection", str(e))

inbox_dir = HOME / "inbox"
if inbox_dir.exists():
    inbox_files = list(inbox_dir.glob("*.md"))
    log(PASS, "Inbox directory", f"{len(inbox_files)} archived messages")
else:
    log(WARN, "Inbox directory missing")

# ================================================================
section("7. SCHEDULER / SELF-TASKS")
# ================================================================

try:
    from fiam_lib.scheduler import load_pending, queue_summary
    pending = load_pending(config)
    summary = queue_summary(config)
    log(PASS, "Scheduler loads", f"{len(pending)} pending tasks")
    log(PASS, "Schedule summary", summary[:80] if summary else "no scheduled tasks (normal)")
except Exception as e:
    log(FAIL, "Scheduler", str(e))

schedule_file = HOME / "self" / "schedule.jsonl"
log(PASS, "schedule.jsonl", f"{sum(1 for _ in open(schedule_file))} entries" if schedule_file.exists() else "not created yet (normal)")

# ================================================================
section("8. SLEEP / SUSPEND (idle timeout)")
# ================================================================

log(PASS, "Idle timeout", f"{config.idle_timeout_minutes} minutes")
log(PASS, "Poll interval", f"{config.poll_interval_seconds} seconds")

lock_file = HOME / "interactive.lock"
if lock_file.exists():
    lock_content = lock_file.read_text().strip()
    lock_age = (time.time() - lock_file.stat().st_mtime) / 60
    log(PASS, "interactive.lock", f"age={lock_age:.1f}min, content={lock_content[:60]}")
else:
    log(PASS, "interactive.lock", "not present (no active session)")

# ================================================================
section("9. WAKE MECHANISM")
# ================================================================

pid_file = STORE / ".fiam.pid"
if pid_file.exists():
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)
        log(PASS, "Daemon running", f"PID {pid}")
    except OSError:
        log(FAIL, "Daemon PID stale", f"PID {pid} not alive")
else:
    log(FAIL, "Daemon not running", "no .fiam.pid")

tg_token = os.environ.get("FIAM_TG_BOT_TOKEN", "")
log(PASS if tg_token else FAIL, "TG bot token", f"set ({len(tg_token)} chars)" if tg_token else "not set")
log(PASS if config.tg_chat_id else FAIL, "TG chat_id", config.tg_chat_id)

if tg_token:
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{tg_token}/getMe"
        resp = json.loads(urllib.request.urlopen(url, timeout=10).read())
        if resp.get("ok"):
            bot = resp["result"]
            log(PASS, "TG bot alive", f"@{bot['username']}")
        else:
            log(FAIL, "TG bot getMe", str(resp))
    except Exception as e:
        log(FAIL, "TG bot getMe", str(e))

active = HOME / "active_session.json"
if active.exists():
    aj = json.loads(active.read_text())
    log(PASS, "Active session", f"id={aj.get('session_id','?')[:12]}...")
else:
    log(WARN, "No active session file")

pipeline_log = CODE / "logs" / "pipeline.log"
if pipeline_log.exists():
    log_lines = pipeline_log.read_text().strip().split("\n")
    wake_lines = [l for l in log_lines if "wake" in l.lower()]
    if wake_lines:
        log(PASS, "Recent wakes", f"{len(wake_lines)} entries, last: {wake_lines[-1][:80]}")
    else:
        log(WARN, "No wake entries in pipeline log")
else:
    log(FAIL, "Pipeline log missing")

# ================================================================
section("10. THREE STATES (notify/mute/block)")
# ================================================================

state_file = HOME / "self" / "comm_state.json"
if state_file.exists():
    state = json.loads(state_file.read_text())
    log(PASS, "State config", json.dumps(state))
else:
    log(WARN, "comm_state.json not found", "3-state system NOT configured yet")

try:
    from fiam_lib.daemon import cmd_start
    import inspect
    src = inspect.getsource(cmd_start)
    if "mute" in src or "block" in src or "notify" in src:
        log(PASS, "State handling in daemon")
    else:
        log(WARN, "State handling in daemon", "notify/mute/block NOT in code")
except Exception as e:
    log(FAIL, "Daemon inspection", str(e))

# ================================================================
section("11. TG STICKERS")
# ================================================================

sticker_index = CODE / "assets" / "stickers" / "index.json"
if sticker_index.exists():
    idx = json.loads(sticker_index.read_text())
    names = [k for k in idx if not k.startswith("_")]
    log(PASS, "Sticker index", f"{len(names)} stickers")
    log(PASS, "Sample stickers", ", ".join(names[:5]))
else:
    log(FAIL, "Sticker index missing")

try:
    from fiam_lib.postman import _extract_stickers, _load_sticker_index
    log(PASS, "Sticker functions importable")
except ImportError as e:
    log(FAIL, "Sticker functions", str(e))

# ================================================================
section("12. DEFER REPLY / PROACTIVE MESSAGING")
# ================================================================

outbox = HOME / "outbox"
if outbox.exists():
    pending_out = list(outbox.glob("*.md"))
    sent_dir = outbox / "sent"
    sent = list(sent_dir.glob("*.md")) if sent_dir.exists() else []
    log(PASS, "Outbox", f"{len(pending_out)} pending, {len(sent)} sent")
else:
    log(FAIL, "Outbox directory missing")

outbox_hook = hooks_dir / "outbox.sh"
if outbox_hook.exists():
    oc = outbox_hook.read_text()
    log(PASS if ("tg" in oc or "email" in oc) else WARN, "outbox.sh extracts markers")

# ================================================================
section("13. SSH TO LOCAL / TUNNEL")
# ================================================================

local_port = 2222
log(PASS, "Tunnel config", f"local_tunnel_port={local_port}")

for label, host, port in [("Local tunnel", "127.0.0.1", local_port), ("DO tunnel (embed)", "127.0.0.1", 8819)]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((host, port))
        s.close()
        log(PASS, label, f"{host}:{port} reachable")
    except Exception as e:
        log(WARN, label, f"{host}:{port} -- {e}")

# ================================================================
section("14. GIT BACKUP")
# ================================================================

try:
    result = subprocess.run(
        ["git", "-C", str(HOME), "log", "--oneline", "-5"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0 and result.stdout.strip():
        commits = result.stdout.strip().split("\n")
        log(PASS, "Git backup", f"{len(commits)} recent commits")
        log(PASS, "Latest commit", commits[0][:80])
    else:
        log(FAIL, "Git log", result.stderr[:80] if result.stderr else "empty")
except Exception as e:
    log(FAIL, "Git backup check", str(e))

compact_hook = hooks_dir / "compact.sh"
if compact_hook.exists():
    cc = compact_hook.read_text()
    log(PASS if "git" in cc else WARN, "compact.sh git operations")

# ================================================================
section("15. CONFIG CONSISTENCY")
# ================================================================

if smtp_port == 465:
    log(PASS, "SMTP port 465 (SSL)", "matches Zoho")
else:
    log(WARN, "SMTP port mismatch", f"{smtp_port} -- should be 465 for Zoho SSL")

log(PASS if Path(config.home_path).exists() else FAIL, "home_path exists", str(config.home_path))

claude_md = HOME / "CLAUDE.md"
if claude_md.exists():
    ctext = claude_md.read_text()
    log(PASS if "tg:Zephyr" in ctext else WARN, "CLAUDE.md has TG markers")
    log(PASS if "recall" in ctext.lower() else WARN, "CLAUDE.md mentions recall")
else:
    log(FAIL, "CLAUDE.md missing")

awareness = HOME / "self" / "awareness.md"
if awareness.exists():
    log(PASS, "awareness.md present", f"{len(awareness.read_text())} chars")
else:
    log(WARN, "awareness.md missing")

# ================================================================
section("SUMMARY")
# ================================================================
passes = sum(1 for r in results if r["tag"] == PASS)
fails = sum(1 for r in results if r["tag"] == FAIL)
warns = sum(1 for r in results if r["tag"] == WARN)
print(f"\n  Total: {len(results)} checks")
print(f"  PASS: {passes}")
print(f"  FAIL: {fails}")
print(f"  WARN: {warns}")
print()

if fails > 0:
    print("  FAILED:")
    for r in results:
        if r["tag"] == FAIL:
            print(f"    - {r['name']}: {r['detail']}")
    print()

if warns > 0:
    print("  WARNINGS:")
    for r in results:
        if r["tag"] == WARN:
            print(f"    - {r['name']}: {r['detail']}")
    print()

log_path = CODE / "logs" / "system_check.log"
with open(log_path, "w") as f:
    f.write(f"fiam system check -- {datetime.now(timezone.utc).isoformat()}\n\n")
    for r in results:
        f.write(f"{r['tag']:4s}  {r['name']}")
        if r["detail"]:
            f.write(f"  -- {r['detail']}")
        f.write("\n")
    f.write(f"\n--- {passes} pass, {fails} fail, {warns} warn ---\n")

print(f"  Log: {log_path}")
#!/usr/bin/env python3
"""fiam system comprehensive check — runs all component tests on ISP."""
import json, os, sys, time, socket, subprocess, signal
from pathlib import Path
from datetime import datetime, timezone

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "src"))
sys.path.insert(0, str(_root / "scripts"))
os.chdir(_root)

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"
SKIP = "⏭️  SKIP"
results = []

def log(tag, name, detail=""):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {tag}  {name}"
    if detail:
        line += f"  — {detail}"
    print(line)
    results.append({"tag": tag, "name": name, "detail": detail})

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# --------------- Config ---------------
from fiam_lib.core import _build_config
import argparse
sys.argv = ["fiam"]
args = argparse.Namespace(config=None, home=None, code=None)
config = _build_config(args)

HOME = Path(config["home_path"])
CODE = Path(config["code_path"])
STORE = CODE / "store"

section("1. RECALL MECHANISM")

# 1a. Events exist
n_events = len(list((STORE / "events").glob("*.json")))
if n_events > 0:
    log(PASS, "Events in store", f"{n_events} events")
else:
    log(FAIL, "Events in store", "0 events — store is empty")

# 1b. Embeddings match events
n_emb = len(list((STORE / "embeddings").glob("*.npy")))
if n_emb == n_events:
    log(PASS, "Embeddings match events", f"{n_emb}/{n_events}")
elif n_emb > 0:
    log(WARN, "Embeddings count mismatch", f"{n_emb} embeddings vs {n_events} events")
else:
    log(FAIL, "No embeddings", "run reindex")

# 1c. Graph edges
graph_file = STORE / "graph.jsonl"
if graph_file.exists():
    n_edges = sum(1 for _ in open(graph_file))
    log(PASS, "Graph edges", f"{n_edges} edges")
else:
    log(FAIL, "Graph file missing", str(graph_file))

# 1d. recall.md exists and is fresh
recall = HOME / "recall.md"
if recall.exists():
    age_min = (time.time() - recall.stat().st_mtime) / 60
    lines = recall.read_text().strip().split("\n")
    log(PASS, "recall.md present", f"{len(lines)} lines, {age_min:.0f}min old")
else:
    log(FAIL, "recall.md missing", str(recall))

# 1e. Retriever joint search works
try:
    from fiam.retriever import joint as joint_retriever
    from fiam.store.home import HomeStore
    store = HomeStore(config)
    events = joint_retriever.search("", store, config)
    log(PASS, "Joint retriever", f"returned {len(events)} events")
except Exception as e:
    log(FAIL, "Joint retriever", str(e))

section("2. HOOK INJECTION")

hooks_dir = HOME / ".claude" / "hooks"
for hook_name in ["inject.sh", "outbox.sh", "boot.sh", "compact.sh"]:
    hook_path = hooks_dir / hook_name
    if hook_path.exists():
        # check executable
        if os.access(hook_path, os.X_OK):
            log(PASS, f"Hook {hook_name}", "present + executable")
        else:
            log(WARN, f"Hook {hook_name}", "present but NOT executable")
    else:
        log(FAIL, f"Hook {hook_name}", "MISSING")

# Check inject.sh reads recall.md
inject = hooks_dir / "inject.sh"
if inject.exists():
    content = inject.read_text()
    if "recall.md" in content:
        log(PASS, "inject.sh reads recall.md")
    else:
        log(WARN, "inject.sh doesn't mention recall.md")
    if "inbox.jsonl" in content:
        log(PASS, "inject.sh reads inbox.jsonl")
    else:
        log(WARN, "inject.sh doesn't mention inbox.jsonl")

# Check settings.local.json hooks config
settings = HOME / ".claude" / "settings.local.json"
if settings.exists():
    sj = json.loads(settings.read_text())
    hooks = sj.get("hooks", {})
    hook_types = list(hooks.keys())
    log(PASS, "settings.local.json", f"hook types: {hook_types}")
else:
    log(FAIL, "settings.local.json missing")

section("3. DRIFT DETECTION")

# Check if _update_recall_if_drifted function exists and works
try:
    from fiam_lib.daemon import _update_recall_if_drifted
    log(PASS, "Drift detection function", "importable")
except ImportError as e:
    log(FAIL, "Drift detection function", str(e))

section("4. GIT DIFF AWARENESS")

# Check if home repo has git
if (HOME / ".git").exists() or (HOME / ".gitattributes").exists():
    log(PASS, "fiet-home is git repo")
else:
    log(FAIL, "fiet-home is not git repo")

# Check home_diff module
try:
    from fiam.injector.home_diff import detect_uncommitted
    diff = detect_uncommitted(config)
    if diff is not None:
        log(PASS, "Git diff detection works", f"{len(diff)} chars of diff")
    else:
        log(PASS, "Git diff detection works", "no uncommitted changes")
except Exception as e:
    log(FAIL, "Git diff detection", str(e))

# Check git features enabled
if config.get("features", {}).get("git_enabled", False):
    log(PASS, "Git features enabled in config")
else:
    log(WARN, "Git features not enabled in config")

section("5. EVENT SEGMENTATION")

try:
    from fiam.extractor import event as event_extractor
    log(PASS, "Event extractor importable")
except Exception as e:
    log(FAIL, "Event extractor import", str(e))

# Check classifier
try:
    from fiam.classifier.emotion import get_classifier
    clf = get_classifier(config)
    log(PASS, "Emotion classifier loaded", type(clf).__name__)
except Exception as e:
    log(FAIL, "Emotion classifier", str(e))

section("6. EMAIL READ/WRITE")

# Check IMAP config
smtp_host = config.get("comms", {}).get("email_smtp_host", "")
smtp_port = config.get("comms", {}).get("email_smtp_port", 0)
email_from = config.get("comms", {}).get("email_from", "")
log(PASS if smtp_host else FAIL, "SMTP host", smtp_host)
log(PASS if smtp_port else FAIL, "SMTP port", str(smtp_port))
log(PASS if email_from else FAIL, "Email from", email_from)

# Check email password env
email_pw = os.environ.get("FIAM_EMAIL_PASSWORD", "")
if email_pw:
    log(PASS, "Email password env", f"set ({len(email_pw)} chars)")
else:
    log(FAIL, "FIAM_EMAIL_PASSWORD", "not set in environment")

# Test IMAP connection
try:
    import imaplib
    conn = imaplib.IMAP4_SSL("imappro.zoho.com", 993)
    conn.login(email_from, email_pw)
    status, data = conn.select("INBOX", readonly=True)
    msg_count = data[0].decode()
    conn.logout()
    log(PASS, "IMAP connection", f"inbox has {msg_count} messages")
except Exception as e:
    log(FAIL, "IMAP connection", str(e))

# Test SMTP connection
try:
    import smtplib
    if smtp_port == 465:
        s = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
    else:
        s = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
        s.starttls()
    s.login(email_from, email_pw)
    s.quit()
    log(PASS, "SMTP connection", f"{smtp_host}:{smtp_port}")
except Exception as e:
    log(FAIL, "SMTP connection", str(e))

# Check inbox directory
inbox_dir = HOME / "inbox"
if inbox_dir.exists():
    inbox_files = list(inbox_dir.glob("*.md"))
    log(PASS, "Inbox directory", f"{len(inbox_files)} archived messages")
else:
    log(WARN, "Inbox directory missing")

section("7. SCHEDULER / SELF-TASKS")

try:
    from fiam_lib.scheduler import load_pending, queue_summary
    pending = load_pending(config)
    summary = queue_summary(config)
    log(PASS, "Scheduler loads", f"{len(pending)} pending tasks")
    if summary:
        log(PASS, "Schedule summary", summary[:80])
    else:
        log(PASS, "Schedule summary", "no scheduled tasks (normal)")
except Exception as e:
    log(FAIL, "Scheduler", str(e))

# Check schedule.jsonl
schedule_file = HOME / "self" / "schedule.jsonl"
if schedule_file.exists():
    n_entries = sum(1 for _ in open(schedule_file))
    log(PASS, "schedule.jsonl", f"{n_entries} entries")
else:
    log(PASS, "schedule.jsonl", "not created yet (normal)")

section("8. SLEEP / SUSPEND (idle timeout)")

idle_timeout = config.get("daemon", {}).get("idle_timeout_minutes", "?")
poll_interval = config.get("daemon", {}).get("poll_interval_seconds", "?")
log(PASS, "Idle timeout config", f"{idle_timeout} minutes")
log(PASS, "Poll interval config", f"{poll_interval} seconds")

# Check interactive.lock mechanism
lock_file = HOME / "interactive.lock"
if lock_file.exists():
    lock_content = lock_file.read_text().strip()
    lock_age = (time.time() - lock_file.stat().st_mtime) / 60
    log(PASS, "interactive.lock present", f"age={lock_age:.1f}min, content={lock_content[:60]}")
else:
    log(PASS, "interactive.lock", "not present (no active session)")

section("9. WAKE MECHANISM")

# Check daemon is running
pid_file = STORE / ".fiam.pid"
if pid_file.exists():
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)
        log(PASS, "Daemon running", f"PID {pid}")
    except OSError:
        log(FAIL, "Daemon PID stale", f"PID {pid} not alive")
else:
    log(FAIL, "Daemon not running", "no .fiam.pid")

# Check TG bot token
tg_token = os.environ.get("FIAM_TG_BOT_TOKEN", "")
tg_chat_id = config.get("comms", {}).get("tg_chat_id", "")
if tg_token:
    log(PASS, "TG bot token", f"set ({len(tg_token)} chars)")
else:
    log(FAIL, "FIAM_TG_BOT_TOKEN", "not set")
log(PASS if tg_chat_id else FAIL, "TG chat_id", tg_chat_id)

# Test TG bot getMe
if tg_token:
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{tg_token}/getMe"
        resp = json.loads(urllib.request.urlopen(url, timeout=10).read())
        if resp.get("ok"):
            bot = resp["result"]
            log(PASS, "TG bot alive", f"@{bot['username']}")
        else:
            log(FAIL, "TG bot getMe", str(resp))
    except Exception as e:
        log(FAIL, "TG bot getMe", str(e))

# Check active session
active = HOME / "active_session.json"
if active.exists():
    aj = json.loads(active.read_text())
    log(PASS, "Active session", f"id={aj.get('session_id','?')[:12]}...")
else:
    log(WARN, "No active session file")

# Check pipeline log for recent wake
pipeline_log = CODE / "logs" / "pipeline.log"
if pipeline_log.exists():
    log_lines = pipeline_log.read_text().strip().split("\n")
    wake_lines = [l for l in log_lines if "wake" in l.lower()]
    if wake_lines:
        log(PASS, "Recent wakes in log", f"{len(wake_lines)} wake entries, last: {wake_lines[-1][:80]}")
    else:
        log(WARN, "No wake entries in pipeline log")
else:
    log(FAIL, "Pipeline log missing")

section("10. THREE STATES (notify/mute/block)")

# Check if state config exists
state_file = HOME / "self" / "comm_state.json"
if state_file.exists():
    state = json.loads(state_file.read_text())
    log(PASS, "State config", json.dumps(state))
else:
    log(WARN, "comm_state.json not found", "3-state system not configured yet")

# Check daemon for state handling
try:
    from fiam_lib.daemon import cmd_start
    import inspect
    src = inspect.getsource(cmd_start)
    if "mute" in src or "block" in src or "notify" in src:
        log(PASS, "State handling in daemon", "found state keywords")
    else:
        log(WARN, "No state handling in daemon", "notify/mute/block not implemented")
except Exception as e:
    log(FAIL, "Daemon inspection", str(e))

section("11. TG STICKERS")

# Check sticker index
sticker_index = CODE / "assets" / "stickers" / "index.json"
if sticker_index.exists():
    idx = json.loads(sticker_index.read_text())
    n_stickers = len([k for k in idx if not k.startswith("_")])
    log(PASS, "Sticker index", f"{n_stickers} stickers")
    # Show a few names
    names = [k for k in idx if not k.startswith("_")][:5]
    log(PASS, "Sample stickers", ", ".join(names))
else:
    log(FAIL, "Sticker index missing", str(sticker_index))

# Check postman sticker functions
try:
    from fiam_lib.postman import _extract_stickers, _load_sticker_index
    log(PASS, "Sticker functions importable")
except ImportError as e:
    log(FAIL, "Sticker functions", str(e))

section("12. DEFER REPLY / PROACTIVE MESSAGING")

# Check outbox structure
outbox = HOME / "outbox"
if outbox.exists():
    pending = [f for f in outbox.glob("*.md")]
    sent_dir = outbox / "sent"
    sent = list(sent_dir.glob("*.md")) if sent_dir.exists() else []
    log(PASS, "Outbox", f"{len(pending)} pending, {len(sent)} sent")
else:
    log(FAIL, "Outbox directory missing")

# Check outbox.sh hook for interactive outbound
outbox_hook = hooks_dir / "outbox.sh"
if outbox_hook.exists():
    content = outbox_hook.read_text()
    if "→tg" in content or "→email" in content:
        log(PASS, "outbox.sh extracts markers")
    else:
        log(WARN, "outbox.sh doesn't extract markers")

section("13. SSH TO LOCAL / TUNNEL")

# Check tunnel config
nodes = config.get("nodes", {})
local_port = nodes.get("local_tunnel_port", 2222)
log(PASS, "Tunnel config", f"local_tunnel_port={local_port}")

# Check if tunnel is alive
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(("127.0.0.1", local_port))
    s.close()
    log(PASS, "Local tunnel alive", f"127.0.0.1:{local_port} reachable")
except Exception as e:
    log(WARN, "Local tunnel down", f"127.0.0.1:{local_port} — {e}")

# Check DO tunnel (embedding API)
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(("127.0.0.1", 8819))
    s.close()
    log(PASS, "DO tunnel alive", "127.0.0.1:8819 reachable")
except Exception as e:
    log(WARN, "DO tunnel down", f"127.0.0.1:8819 — {e}")

section("14. GIT BACKUP")

# Check if fiet-home has git log
try:
    result = subprocess.run(
        ["git", "-C", str(HOME), "log", "--oneline", "-5"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0 and result.stdout.strip():
        commits = result.stdout.strip().split("\n")
        log(PASS, "Git backup", f"{len(commits)} recent commits")
        log(PASS, "Latest commit", commits[0][:80])
    else:
        log(FAIL, "Git log", result.stderr[:80] if result.stderr else "empty")
except Exception as e:
    log(FAIL, "Git backup check", str(e))

# Check auto-commit in hooks (compact.sh does this)
compact_hook = hooks_dir / "compact.sh"
if compact_hook.exists():
    content = compact_hook.read_text()
    if "git" in content:
        log(PASS, "compact.sh has git operations")
    else:
        log(WARN, "compact.sh missing git operations")

section("15. FIAM TOML CONFIG CONSISTENCY")

# Check SMTP port (we changed to 465 on ISP)
if smtp_port == 465:
    log(PASS, "SMTP port is 465 (SSL)", "matches Zoho")
else:
    log(WARN, "SMTP port", f"{smtp_port} — should be 465 for Zoho SSL")

# Check home_path
home_cfg = config.get("home_path", "")
if Path(home_cfg).exists():
    log(PASS, "home_path exists", home_cfg)
else:
    log(FAIL, "home_path doesn't exist", home_cfg)

# =============== SUMMARY ===============
section("SUMMARY")
passes = sum(1 for r in results if r["tag"] == PASS)
fails = sum(1 for r in results if r["tag"] == FAIL)
warns = sum(1 for r in results if r["tag"] == WARN)
skips = sum(1 for r in results if r["tag"] == SKIP)
print(f"\n  Total: {len(results)} checks")
print(f"  {PASS}: {passes}")
print(f"  {FAIL}: {fails}")
print(f"  {WARN}: {warns}")
print(f"  {SKIP}: {skips}")
print()

if fails > 0:
    print("  FAILED checks:")
    for r in results:
        if r["tag"] == FAIL:
            print(f"    - {r['name']}: {r['detail']}")
    print()

if warns > 0:
    print("  WARNINGS:")
    for r in results:
        if r["tag"] == WARN:
            print(f"    - {r['name']}: {r['detail']}")
    print()

# Write results to log
log_path = CODE / "logs" / "system_check.log"
with open(log_path, "w") as f:
    f.write(f"fiam system check — {datetime.now(timezone.utc).isoformat()}\n\n")
    for r in results:
        f.write(f"{r['tag']}  {r['name']}")
        if r["detail"]:
            f.write(f"  — {r['detail']}")
        f.write("\n")
    f.write(f"\n--- {passes} pass, {fails} fail, {warns} warn ---\n")

print(f"  Log written to: {log_path}")
