// API client for fiam dashboard backend.
// Reads apiBase + token from appConfig (Settings page) at CALL TIME, falling
// back to build-time env (VITE_API_BASE / VITE_INGEST_TOKEN) for dev / proxy.

import { appConfig } from "../config"

function getBase(): string {
  // appConfig.apiBase wins; trim trailing slash so we always do `${base}/api/...`.
  const v = (appConfig.apiBase || (import.meta.env.VITE_API_BASE as string) || "").trim()
  return v.replace(/\/+$/, "")
}

function getToken(): string {
  return (appConfig.ingestToken || (import.meta.env.VITE_INGEST_TOKEN as string) || "").trim()
}

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" }
  // Always inject token when we have one — the dev vite proxy strips it harmlessly.
  const t = getToken()
  if (t) h["X-Fiam-Token"] = t
  return h
}

function orKey(): string {
  return (appConfig.openrouterKey || "").trim()
}

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
  const payload = {
    files: await Promise.all(
      files.map(async (f) => ({
        name: f.name,
        mime: f.type || "application/octet-stream",
        data: await fileToBase64(f),
      })),
    ),
  }
  const res = await fetch(`${getBase()}/api/app/upload`, {
    method: "POST",
    headers: authHeaders(),
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
  const body: Record<string, unknown> = { text, source, backend, attachments }
  const ork = orKey()
  if (ork) body.openrouter_key = ork
  // eslint-disable-next-line no-console
  console.log("[api] sendChat ->", `${getBase()}/api/app/chat`, { hasToken: !!getToken(), hasOR: !!ork })
  const res = await fetch(`${getBase()}/api/app/chat`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
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
  try {
    const res = await fetch(`${getBase()}${path}`, {
      method: "POST",
      headers: authHeaders(),
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

// cutFlow: drop a divider marker into the unprocessed flow. The next
//          processFlow() call will use these markers to split beats into
//          multiple events. Cutting alone does NOT trigger DS work.
export function cutFlow() {
  return postMemoryOp("/api/app/cut")
}

// processFlow: ask the server to seal all unprocessed beats into events
//              (using cut markers as segment dividers). Synchronous —
//              resolves only when DS is done. UI should disable Send and
//              show the hourglass animation while this is in flight.
export function processFlow() {
  return postMemoryOp("/api/app/process")
}

// Back-compat alias.
export function sealEvent() {
  return postMemoryOp("/api/app/process")
}
