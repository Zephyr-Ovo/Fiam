// API client for fiam dashboard backend.
// In dev: requests go through vite proxy at /api → http://127.0.0.1:8766
//         token is injected by vite proxy from VITE_INGEST_TOKEN.
// In prod (Capacitor): set VITE_API_BASE to full URL and VITE_INGEST_TOKEN at build.

import { appConfig } from "../config"

const API_BASE = (import.meta.env.VITE_API_BASE as string) || ""
const INGEST_TOKEN = (import.meta.env.VITE_INGEST_TOKEN as string) || ""

export type ChatThought = {
  kind: "think" | "search" | "check" | "native"
  text: string
  result?: string
  source: "marker" | "native"
}

export type ChatAttachment = {
  path: string
  name: string
  mime?: string
  size?: number
}

export type ChatResponse = {
  ok: boolean
  backend?: "cc" | "api"
  reply: string
  thoughts?: ChatThought[]
  thoughts_locked?: boolean
  session_id?: string
  cost_usd?: number
  model?: string
  recall?: unknown
  error?: string
}

export type UploadResponse = {
  ok: boolean
  files?: ChatAttachment[]
  error?: string
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error)
    reader.onload = () => {
      const r = reader.result as string
      const idx = r.indexOf(",")
      resolve(idx >= 0 ? r.slice(idx + 1) : r)
    }
    reader.readAsDataURL(file)
  })
}

export async function uploadFiles(files: File[]): Promise<UploadResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (API_BASE && INGEST_TOKEN) headers["X-Fiam-Token"] = INGEST_TOKEN
  const payload = {
    files: await Promise.all(
      files.map(async (f) => ({
        name: f.name,
        mime: f.type || "application/octet-stream",
        data: await fileToBase64(f),
      })),
    ),
  }
  const res = await fetch(`${API_BASE}/api/app/upload`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as UploadResponse
}

export async function sendChat(
  text: string,
  source = "favilla",
  attachments: ChatAttachment[] = [],
  backend: "cc" | "api" = appConfig.defaultBackend,
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  // In native (no proxy), inject token directly. In dev, vite proxy adds it.
  if (API_BASE && INGEST_TOKEN) headers["X-Fiam-Token"] = INGEST_TOKEN
  const res = await fetch(`${API_BASE}/api/app/chat`, {
    method: "POST",
    headers,
      body: JSON.stringify({ text, source, backend, attachments }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    return { ok: false, reply: "", error: data.error || `HTTP ${res.status}` }
  }
  return data as ChatResponse
}

// --- Memory operations (manual mode) ---
//
// recallNow:  user-triggered recall refresh. Server writes recall.md so the
//             AI sees relevant past events on the NEXT message. Does not chat.
// sealEvent:  user-triggered manual cut. Server packages everything since the
//             previous cut into one event (compute embedding, write fingerprint,
//             ask DS for graph edges, name it). Async-heavy; UI fires & forgets.
//
// Both endpoints are stubs at the moment — backend wiring comes after the
// CC-path beat ingestion fix. UI calls them anyway so the contract is fixed.

export type MemoryOpResponse = { ok: boolean; error?: string; [k: string]: unknown }

async function postMemoryOp(path: string, body: object = {}): Promise<MemoryOpResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (API_BASE && INGEST_TOKEN) headers["X-Fiam-Token"] = INGEST_TOKEN
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify({ source: "favilla", ...body }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
    return { ok: true, ...data }
  } catch (e) {
    return { ok: false, error: String(e) }
  }
}

export function recallNow() {
  return postMemoryOp("/api/app/recall")
}

export function sealEvent() {
  return postMemoryOp("/api/app/seal")
}
