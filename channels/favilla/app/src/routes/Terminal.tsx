import { useState, useRef, useEffect } from "react"
import { sendChatStream } from "../lib/api"
import type { StreamChatEvent } from "../lib/api"
import { appConfig } from "../config"
import { ArrowLeft, Send } from "lucide-react"

type Line = { role: "you" | "ai" | "sys"; text: string }

export function Terminal({ onBack }: { onBack: () => void }) {
  const [lines, setLines] = useState<Line[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [lines])

  async function send() {
    const text = input.trim()
    if (!text || busy) return
    setInput("")
    setLines((p) => [...p, { role: "you", text }])
    setBusy(true)
    let buf = ""
    try {
      await sendChatStream(text, "chat", [], appConfig.defaultRuntime, (ev: StreamChatEvent) => {
        if (ev.event === "text_delta") {
          buf += ev.data.text
          setLines((p) => {
            const last = p[p.length - 1]
            if (last?.role === "ai") return [...p.slice(0, -1), { role: "ai", text: buf }]
            return [...p, { role: "ai", text: buf }]
          })
        }
        if (ev.event === "error") {
          setLines((p) => [...p, { role: "sys", text: ev.data.message }])
        }
      })
    } catch (e) {
      setLines((p) => [...p, { role: "sys", text: String(e) }])
    }
    setBusy(false)
    inputRef.current?.focus()
  }

  const aiLabel = (appConfig.aiName || "ai").toLowerCase()
  const roleColor = { you: "#d99477", ai: "#8b9a6b", sys: "#c0392b" }

  return (
    <div className="flex flex-col h-full select-text" style={{ background: "#1a1612", color: "#d4c8b8", fontFamily: "var(--font-mono, ui-monospace, SFMono-Regular, Consolas, monospace)" }}>
      <div className="flex items-center h-11 px-3 shrink-0" style={{ borderBottom: "1px solid rgba(212,200,184,0.12)" }}>
        <button type="button" onClick={onBack} className="w-8 h-8 flex items-center justify-center rounded-lg active:opacity-60" style={{ color: "#d4c8b8" }}>
          <ArrowLeft size={18} />
        </button>
        <span className="ml-1 text-[13px] opacity-50">terminal</span>
        <span className="ml-auto text-[11px] opacity-30 tracking-wide">{aiLabel}@isp</span>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-2" style={{ fontSize: 13, lineHeight: 1.6 }}>
        {lines.length === 0 && (
          <div className="opacity-25 text-[12px] pt-1">connected. type to begin.</div>
        )}
        {lines.map((line, i) => (
          <div key={i} className="mb-0.5">
            <span style={{ color: roleColor[line.role] }}>
              {line.role === "you" ? "you" : line.role === "sys" ? "sys" : aiLabel}
            </span>
            <span className="opacity-30"> › </span>
            <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{line.text}</span>
          </div>
        ))}
        {busy && <span className="opacity-40 animate-pulse text-[15px]">▋</span>}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send() }}
        className="flex items-center gap-2 px-3 py-2.5 shrink-0"
        style={{ borderTop: "1px solid rgba(212,200,184,0.12)" }}
      >
        <span className="opacity-30 text-[14px]">›</span>
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="flex-1 bg-transparent outline-none text-[13px]"
          style={{ color: "#d4c8b8", caretColor: "#d99477" }}
          placeholder={busy ? "waiting…" : ""}
          disabled={busy}
          autoFocus
        />
        <button type="submit" disabled={busy || !input.trim()} className="opacity-40 active:opacity-100 disabled:opacity-15 transition-opacity" style={{ color: "#d99477" }}>
          <Send size={15} />
        </button>
      </form>
    </div>
  )
}
