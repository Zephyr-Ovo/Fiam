import json, urllib.request, os, sys

key = os.environ["OPENROUTER_API_KEY"]
model = sys.argv[1] if len(sys.argv) > 1 else "deepseek/deepseek-chat-v3-0324:free"
body = {
    "model": model,
    "messages": [
        {"role": "user", "content": "Call list_dir with path='self', then briefly say what you saw."}
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List directory entries.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ],
    "max_tokens": 200,
}
req = urllib.request.Request(
    "https://openrouter.ai/api/v1/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
)
try:
    r = urllib.request.urlopen(req, timeout=30)
    data = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:500])
    sys.exit(1)

print(f"== {model} ==")
print("finish:", data["choices"][0]["finish_reason"])
print("msg:", json.dumps(data["choices"][0]["message"], indent=2, ensure_ascii=False)[:800])
