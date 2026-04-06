# fiam — Debug Guide

Diagnostic workflows, hook testing, and common issues.

---

## Hook testing

The hook injects `recall.md` as `additionalContext` on every Claude Code prompt.
Use the reference scripts in `developer/hooks/` to test the injection in isolation
before using `fiam init`.

### Windows (PowerShell)

```powershell
# Create a test project
mkdir $env:USERPROFILE\test-fiam-hook
mkdir $env:USERPROFILE\test-fiam-hook\.claude\hooks

# Copy reference hook + settings
Copy-Item developer\hooks\inject.ps1 $env:USERPROFILE\test-fiam-hook\.claude\hooks\inject.ps1
Copy-Item developer\hooks\settings.local.json $env:USERPROFILE\test-fiam-hook\.claude\settings.local.json

# Write a test memory
Set-Content $env:USERPROFILE\test-fiam-hook\recall.md "用户有一只猫叫小橘，今年3岁，最喜欢趴在键盘上。"

# Start Claude Code from the test project
cd $env:USERPROFILE\test-fiam-hook
claude
```

Then ask: **"你知道我有宠物吗？"** — CC should reply with 小橘 without being told.

### macOS / Linux

```bash
mkdir -p ~/test-fiam-hook/.claude/hooks
cp developer/hooks/inject.sh ~/test-fiam-hook/.claude/hooks/inject.sh
chmod +x ~/test-fiam-hook/.claude/hooks/inject.sh
cp developer/hooks/settings.local.bash.json ~/test-fiam-hook/.claude/settings.local.json

echo "用户有一只猫叫小橘，今年3岁，最喜欢趴在键盘上。" > ~/test-fiam-hook/recall.md
cd ~/test-fiam-hook && claude
```

### Verifying injection

In Claude Code, run: `/hooks`

You should see "1 hook configured" under UserPromptSubmit. The status message
"checking memory..." should appear briefly before each assistant response.

To inspect raw hook output, run the script directly:

```powershell
$env:CLAUDE_PROJECT_DIR = "$env:USERPROFILE\test-fiam-hook"
& $env:USERPROFILE\test-fiam-hook\.claude\hooks\inject.ps1
```

Expected output (one JSON line):
```json
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"用户有一只猫叫小橘..."}}
```

### How `additionalContext` enters CC

`additionalContext` is injected into the **system/context layer** — not as a user
message. Claude Code sees it as background knowledge, not as something the user
just said. This produces naturalistic recall ("I know you have a cat") rather
than explicit reference ("you just told me you have a cat").

---

## Diagnosing recall.md content

```powershell
# Check what is currently in recall.md
Get-Content (fiam status | Select-String "home:" | ForEach-Object { $_ -replace "  home: ", "" }) | Join-Path -ChildPath "recall.md" | Get-Content
```

Or directly:
```powershell
Get-Content <your-home>\recall.md
```

The file has an HTML comment header `<!-- recall | timestamp -->` which is
stripped by the hook. The bullet points are what CC receives.

---

## Diagnosing the daemon

### It's not processing

1. Check it's running: `fiam status`
2. Check the JSONL directory exists: `fiam find-sessions --home <your-home>`
3. Check if the idle timeout hasn't passed yet (default: 30 min of no activity)
4. Run manually: `fiam post --debug`

### It processed but no recall update

The significance gate (arousal_threshold, default 0.6) may be filtering everything.
Check `logs/sessions/<latest>/extractor.json` — look at `arousal` scores.

Lower the threshold temporarily:

```toml
# fiam.toml
arousal_threshold = 0.4
```

### Events stored but retrieval returns nothing

Events have a `min_event_age_hours` gate (default: 6h). Newly stored events
are intentionally skipped in retrieval to avoid writing about the current session.

For testing, lower it:

```toml
# fiam.toml
min_event_age_hours = 0
```

---

## Reading session logs

```
logs/sessions/MMDD_HHMM/
  extractor.json    — per-turn extraction: arousal, valence, topics, triggers
  report.json       — full structured pipeline output
  report.md         — human-readable: what was stored, why, what was skipped
```

`report.md` is the fastest way to understand what happened. If events were
skipped, the reason (significance gate, duplicate, too recent) is noted there.

---

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `no fiam.toml found` | Not initialized | `fiam init` |
| `No project directory found` | Claude Code never opened that home | Open CC from home first |
| Hook shows 0 hooks configured | settings.local.json not in home/.claude/ | `fiam init` re-runs hook install |
| recall.md exists but CC ignores it | Hook not firing | Check `/hooks` in CC |
| 0 events stored after scan | All turns below arousal threshold | Lower `arousal_threshold` in fiam.toml |
| `fiam start` says already running | Stale PID file | `fiam stop` then `fiam start` |
