import { useState, useRef, useEffect, useCallback } from "react"
import { appConfig } from "../config"
import { ArrowLeft } from "lucide-react"

export function Terminal({ onBack }: { onBack: () => void }) {
  const [lines, setLines] = useState<string[]>([])
  const [input, setInput] = useState("")
  const [connected, setConnected] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const bufRef = useRef("")

  const pushText = useCallback((text: string) => {
    bufRef.current += text
    const parts = bufRef.current.split("\n")
    bufRef.current = parts.pop() || ""
    if (parts.length > 0) {
      setLines((p) => {
        const updated = [...p]
        if (updated.length > 0) {
          updated[updated.length - 1] += parts[0]
          parts.shift()
        }
        updated.push(...parts)
        while (updated.length > 500) updated.shift()
        return updated
      })
    }
    if (bufRef.current) {
      setLines((p) => {
        const updated = [...p]
        if (updated.length > 0) {
          updated[updated.length - 1] = updated[updated.length - 1].split("\r").pop() || ""
        }
        return updated
      })
    }
  }, [])

  useEffect(() => {
    const base = (appConfig.apiBase || "").trim().replace(/\/+$/, "")
    const wsBase = base.replace(/^http/, "ws").replace(/:\d+/, ":8767")
    const wsUrl = wsBase || "ws://127.0.0.1:8767"

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      setLines(["connected."])
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        const cleaned = ev.data.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "").replace(/\r/g, "")
        pushText(cleaned)
      } else if (ev.data instanceof Blob) {
        ev.data.text().then((t) => {
          const cleaned = t.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, "").replace(/\r/g, "")
          pushText(cleaned)
        })
      }
    }
    ws.onerror = () => setLines((p) => [...p, "[connection error]"])
    ws.onclose = () => {
      setConnected(false)
      setLines((p) => [...p, "[disconnected]"])
    }

    return () => { ws.close() }
  }, [pushText])

  function send() {
    const text = input.trim()
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(text + "\n")
    setInput("")
  }

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })
  }, [lines])

  return (
    <div className="flex flex-col h-full select-text" style={{ background: "#1a1612", color: "#d4c8b8", fontFamily: "var(--font-mono, ui-monospace, SFMono-Regular, Consolas, monospace)" }}>
      <div className="flex items-center h-11 px-3 shrink-0" style={{ borderBottom: "1px solid rgba(212,200,184,0.12)" }}>
        <button type="button" onClick={onBack} className="w-8 h-8 flex items-center justify-center rounded-lg active:opacity-60" style={{ color: "#d4c8b8" }}>
          <ArrowLeft size={18} />
        </button>
        <span className="ml-1 text-[13px] opacity-50">terminal</span>
        <span className="ml-auto text-[11px] tracking-wide" style={{ color: connected ? "#8b9a6b" : "#c0392b" }}>
          {connected ? "● connected" : "○ offline"}
        </span>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-2" style={{ fontSize: 12, lineHeight: 1.55 }}>
        {lines.map((line, i) => (
          <div key={i} style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", minHeight: "1.2em" }}>
            {line}
          </div>
        ))}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send() }}
        className="flex items-center gap-2 px-3 py-2.5 shrink-0"
        style={{ borderTop: "1px solid rgba(212,200,184,0.12)" }}
      >
        <span className="opacity-30 text-[14px]">$</span>
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="flex-1 bg-transparent outline-none text-[13px]"
          style={{ color: "#d4c8b8", caretColor: "#d99477" }}
          placeholder=""
          disabled={!connected}
          autoFocus
        />
      </form>
    </div>
  )
}
