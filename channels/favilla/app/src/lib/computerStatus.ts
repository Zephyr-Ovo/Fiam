// Live computer-control activity feed (browser + desktop).
// Subscribes to /favilla/computer/events via EventSource. No polling.

import { useEffect, useRef, useState } from "react"
import { appConfig } from "../config"

export type ComputerEvent = {
  id: number
  event: string // "info" | "act"
  ts: number
  data: {
    surface?: "b" | "d" // browser | desktop
    kind?: string
    label?: string
    node?: string
    ok?: boolean
    text?: string
    reply?: string
    actions?: Array<{ action?: string; node?: string; name?: string }>
    done?: { reason?: string } | null
  }
}

function getBase(): string {
  const v = (appConfig.apiBase || (import.meta.env.VITE_API_BASE as string) || "").trim()
  return v.replace(/\/+$/, "")
}
function getToken(): string {
  return (appConfig.ingestToken || (import.meta.env.VITE_INGEST_TOKEN as string) || "").trim()
}

const MAX_EVENTS = 20

export function useComputerStatus(enabled = true): {
  events: ComputerEvent[]
  connected: boolean
  latest: ComputerEvent | null
} {
  const [events, setEvents] = useState<ComputerEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [configVersion, setConfigVersion] = useState(0)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    const onConfigChanged = () => setConfigVersion((v) => v + 1)
    window.addEventListener("favilla:config-changed", onConfigChanged)
    return () => window.removeEventListener("favilla:config-changed", onConfigChanged)
  }, [])

  useEffect(() => {
    const base = getBase()
    const token = getToken()
    if (!enabled) return
    if (!token) return
    const url = `${base}/favilla/computer/events?token=${encodeURIComponent(token)}`
    const es = new EventSource(url)
    esRef.current = es
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    const handler = (msg: MessageEvent) => {
      try {
        const data = JSON.parse(msg.data)
        const ev: ComputerEvent = {
          id: Number(msg.lastEventId || 0),
          event: msg.type,
          ts: Date.now() / 1000,
          data,
        }
        setEvents((prev) => {
          const next = [...prev, ev]
          return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next
        })
      } catch {
        // ignore malformed
      }
    }
    es.addEventListener("info", handler as EventListener)
    es.addEventListener("act", handler as EventListener)
    return () => {
      es.close()
      esRef.current = null
      setConnected(false)
    }
  }, [configVersion, enabled])

  const latest = events.length > 0 ? events[events.length - 1] : null
  return { events, connected, latest }
}

export function describeEvent(ev: ComputerEvent): string {
  const d = ev.data || {}
  const surf = d.surface === "d" ? "desktop" : "browser"
  if (ev.event === "act") {
    const ok = d.ok === false ? "✗" : "✓"
    return `${ok} ${surf} ${d.kind || ""} ${d.label || d.node || ""}`.trim()
  }
  // info
  if (d.kind === "done") {
    return `${surf} done: ${d.done?.reason || ""}`.trim()
  }
  if (d.kind === "decision" && d.actions && d.actions.length > 0) {
    const a = d.actions[0]
    return `${surf} → ${a.action || ""} ${a.name || a.node || ""}`.trim()
  }
  if (d.reply) {
    const r = d.reply.length > 60 ? d.reply.slice(0, 60) + "…" : d.reply
    return `${surf} note: ${r}`
  }
  return `${surf} ${d.kind || ""}`.trim()
}
