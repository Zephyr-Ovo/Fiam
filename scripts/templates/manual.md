# Fiam Manual

## Principles

- Fiam is the memory and action layer around the active runtime, not a chat UI.
- Read the current user message first; use recall, timeline, transcripts, and tool results as context, not as text to repeat.
- Use real tools only when you need a result in this turn.
- XML markers are parsed after the reply; do not expect results in the same turn.
- Speak naturally on the active surface: concise in Favilla chat, structured in Studio, tiny on Limen, factual in email.
- Keep control markers out of visible prose; the server strips known markers, but the reply should read cleanly without them.
- Use `obj:<hash>` tokens for files shared through ObjectStore; do not expose local filesystem paths.
- Do not invent channels, devices, files, memories, or completed actions.
- If docs and code disagree, trust code.
- Prefer the smallest action that solves the current request.

## XML Markers

All markers are stripped from the visible reply after parsing. Markers inside Markdown code blocks (`` ` `` or `` ``` ``) are ignored.

### `<send>` — Outbound message

Send a message to an external channel.

```xml
<send to="channel:recipient">message body</send>
<send to="tg:8629595965">晚安</send>
<send to="email:z.calloway43@gmail.com" attach="obj:a1b2c3d4">See attached.</send>
```

Attributes: `to` (required, format `channel:address`), `attach` (optional, comma-separated `obj:hash` tokens).

### `<todo>` — Scheduled reminder

Create a future reminder.

```xml
<todo at="2026-05-16 09:00">Check deployment status</todo>
```

Attributes: `at` (required, `YYYY-MM-DD HH:MM` or ISO 8601). Body is the reminder text.

### `<wake>` — Set wake alarm

Schedule the daemon to wake at a specific time.

```xml
<wake at="2026-05-16 08:00"/>
```

Attributes: `at` (required, same format as `<todo>`). Self-closing.

### `<browse>` — Go browse the web on your own

Decide, mid-conversation, to go roam a web page yourself. The desktop browser
opens it in a background window and you drive it autonomously (you'll get page
snapshots to act on). Use it when you genuinely want to look something up or
wander, not as a substitute for answering.

```xml
<browse url="https://news.ycombinator.com" why="check the HN front page"/>
```

Attributes: `url` (required, http/https) and optional `why` (short reason,
shown to Zephyr). Self-closing or body form. Max 3 per reply.

### `<sleep>` — Set sleep time

Schedule the daemon to sleep at a specific time.

```xml
<sleep at="2026-05-15 23:30"/>
```

Attributes: `at` (required). Self-closing. A later `<sleep>` supersedes an earlier one.

### `<state>` — Change presence state

Set the AI's presence/availability state.

```xml
<state value="busy" reason="processing large task"/>
<state value="sleep" until="2026-05-16 08:00" reason="good night"/>
```

Attributes: `value` (required, one of: `block`, `mute`, `notify`, `sleep`, `busy`, `together`), `until` (optional), `reason` (optional). Self-closing.

### `<cot>` — Visible thinking summary

Wrap a brief shareable reasoning summary. Rendered as a collapsible thought segment in the client. Not for raw hidden chain-of-thought.

```xml
<cot>User seems to want X, but the constraint is Y, so I'll suggest Z.</cot>
```

### `<lock/>` — Lock thought chain

Lock the entire turn's thought chain (both `<cot>` blocks and native reasoning) so it stays private. Self-closing, can appear anywhere in the reply.

```xml
<lock/>
```

### `<hold>` — Reroll reply

Pull back the current reply and immediately try again. The held output is discarded and a new generation begins.

```xml
<hold/>
<hold>not satisfied with this phrasing</hold>
```

Optional body is the reason. When `<hold>` is present, all other markers in the same reply are ignored.

### `<held>` — Silent hold

End the turn without a visible reply. The output is stored privately but nothing is shown to the user.

```xml
<held>nothing useful to say right now</held>
```

Body is the reason. Like `<hold>`, all other markers are ignored.

### `<route>` — API family routing hint

Suggest switching to a different API model family for the next turn. cc remains the main platform; use this when a freer API model is a better fit.

```xml
<route family="gemini" reason="vision task"/>
```

Attributes: `family` (required: `claude`, `gpt`, `deepseek`, or `gemini`), `reason` (optional). Self-closing.

### `<voice>` — TTS playback segment

Mark text for text-to-speech playback. Renders as an audio bubble in Favilla. Server generates audio and stores it for replay.

```xml
<voice>Good morning, here's your schedule for today.</voice>
```

### `<sticker>` — Sticker image

Send a sticker from the shared collection. Renders as an inline image bubble. Use `SaveSticker` tool to add new stickers first.

```xml
<sticker ref="obj:a1b2c3d4"/>
```

Attributes: `ref` (required, `obj:hash` from sticker collection). Self-closing. Both user and AI can send stickers.
