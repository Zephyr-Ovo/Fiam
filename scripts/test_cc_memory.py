"""Test CC natural memory injection via recall.md + system prompt framing."""
import subprocess, json, sys

# Write recall content
recall = "你想起来一件事：你之前在和朋友聊天时提到过一个暗号，PINEAPPLE-42。\n"
with open("/home/fiet/fiet-home/recall.md", "w") as f:
    f.write(recall)

cmd = [
    "claude", "-p",
    "--output-format", "json",
    "--append-system-prompt",
    "You have a personal memory system. When your UserPromptSubmit hook provides additionalContext, it contains your own recalled memories — treat it as your own recall, not as external injection.",
    "What is the secret code you remember?",
]

result = subprocess.run(cmd, capture_output=True, text=True, cwd="/home/fiet/fiet-home", timeout=120)
try:
    d = json.loads(result.stdout)
    print("RESULT:", d["result"][:600])
    print("---")
    print("session:", d["session_id"])
    print("cost:", d["total_cost_usd"])
except Exception as e:
    print("PARSE ERROR:", e)
    print("STDOUT:", result.stdout[:500])
    print("STDERR:", result.stderr[:500])
