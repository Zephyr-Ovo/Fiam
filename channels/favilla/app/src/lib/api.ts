// API client for the Favilla server.
// Reads apiBase + token from appConfig (Settings page) at CALL TIME, falling
// back to build-time env (VITE_API_BASE / VITE_INGEST_TOKEN) for dev / proxy.

import { appConfig } from "../config"
import type { StrollSpatialContext, StrollSpatialRecord, StrollTrackPoint } from "@stroll-map/types"

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

export type ChatThought = {
  kind: "think" | "search" | "check" | "native"
  text: string
  summary?: string
  result?: string
  source: "marker" | "native" | "official" | "fiam"
  locked?: boolean
  icon?: string
}

export type ChatSegment =
  | { type: "text"; text: string }
  | {
      type: "thought"
      kind?: "think" | "search" | "check" | "native"
      text?: string
      summary?: string
      result?: string
      source?: "marker" | "native" | "official" | "fiam"
      locked?: boolean
      icon?: string
    }
  | {
      // Native tool call (cc Bash/Read/Grep/Glob/Edit/Write/...).
      // Server emits one tool_use then later a tool_result with matching id.
      type: "tool_use"
      tool_use_id?: string
      tool_name?: string
      input_summary?: string
    }
  | {
      type: "tool_result"
      tool_use_id?: string
      tool_name?: string
      result_summary?: string
      is_error?: boolean
    }
  | { type: "voice"; text: string; object_hash?: string }
  | { type: "sticker"; name?: string; ref?: string; object_hash?: string }

export type ChatAttachment = {
  object_hash?: string
  path: string
  name: string
  mime?: string
  size?: number
}

export type StoredChatMessage = {
  id: string
  role: "user" | "ai"
  t: number
  text?: string
  raw_text?: string
  runtime?: "cc" | "api" | string
  attachments?: Array<{ kind: "voice" | "file" | "image"; name: string; size?: string | number; path?: string; object_hash?: string; mime?: string; duration?: number; seconds?: number }>
  thinking?: ChatThought[]
  thinkingLocked?: boolean
  segments?: ChatSegment[]
  hold?: { queued?: number; immediate?: boolean }
  divider?: { kind: "scissor" | "recall"; label?: string }
  recallUsed?: boolean
  error?: boolean
}

export type ChatResponse = {
  ok: boolean
  runtime?: "cc" | "api"
  reply: string
  thoughts?: ChatThought[]
  thoughts_locked?: boolean
  segments?: ChatSegment[]
  hold?: { queued?: number; immediate?: boolean }
  session_id?: string
  cost_usd?: number
  model?: string
  transcript_id?: string
  attachments?: StoredChatMessage["attachments"]
  trace?: Record<string, unknown>
  recall?: unknown
  stroll_context?: StrollSpatialContext
  stroll_records?: StrollSpatialRecord[]
  stroll_actions?: StrollClientAction[]
  error?: string
}

export type StrollClientAction = { id: string; type: string; status: string; payload?: Record<string, unknown> }

export type UploadResponse = {
  ok: boolean
  files?: ChatAttachment[]
  error?: string
}

export type TranscriptResponse = {
  ok: boolean
  messages?: StoredChatMessage[]
  error?: string
}

export type StrollRecordResponse = {
  ok: boolean
  record?: StrollSpatialRecord
  error?: string
}

export type StrollNearbyResponse = {
  ok: boolean
  records?: StrollSpatialRecord[]
  contextVersion?: string
  error?: string
}

export type DashboardDayBucket = {
  turns: number
  user_words: number
  ai_words: number
  emoji?: string
}

export type DashboardHistoryDigest = {
  turns: number
  user_turns: number
  ai_turns: number
  words: number
  user_words: number
  ai_words: number
  by_day: Record<string, DashboardDayBucket>
  content_units?: number
  events?: Array<Record<string, unknown>>
}

export type RingTodayData = {
  date: string
  synced_at?: string
  current_hr?: number
  resting_hr?: number
  max_hr?: number
  steps?: number
  calories?: number
  distance_m?: number
  hr_series?: Array<{ time: string; hr: number }>
}

export type DashboardLocationBucket = {
  name: string
  words: number
  percent: number
  count?: number
  emoji?: string
  latest_at?: string | number
  placeKind?: string
}

export type DashboardSummary = {
  ok: boolean
  status?: {
    daemon?: string
    events?: number
    embeddings?: number
    flow_beats?: number
    thinking_beats?: number
    interaction_beats?: number
    home?: string
  }
  health?: {
    daemon?: string
    pending_todos?: number
    retry_todos?: number
    budget_ok?: boolean
    last_pipeline_error?: string | null
  }
  events?: Array<{ id: string; time?: string; preview?: string; access_count?: number }>
  todos?: Array<{ at: string; type?: string; reason?: string }>
  chat?: DashboardHistoryDigest
  stroll?: DashboardHistoryDigest
  studio?: DashboardHistoryDigest
  locations?: DashboardLocationBucket[]
  ring?: RingTodayData
  error?: string
}

export type StudioWorkspaceState = {
  version?: number
  updated_at?: string
  files: unknown[]
  activeFileId: string
  activeNoteContent: string
  fileContents?: Record<string, string>
  timeline: unknown[]
}

export type StudioStateResponse = {
  ok: boolean
  state?: StudioWorkspaceState | null
  error?: string
}

export type StudioEditCommand = {
  op: "replace" | "insert_after" | "insert_before" | "delete" | "append" | "prepend"
  target?: string
  text?: string
  note?: string
}

export type StudioEditResponse = {
  ok: boolean
  summary?: string
  author?: string
  edits?: StudioEditCommand[]
  runtime?: "api" | "cc"
  model?: string
  error?: string
}

export type StudioEditRequest = {
  instruction: string
  content: string
  fileId?: string
  fileName?: string
  runtime?: "auto" | "api" | "cc"
  location?: Record<string, unknown>
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
  const res = await fetch(`${getBase()}/favilla/upload`, {
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
  source = "chat",
  attachments: ChatAttachment[] = [],
  runtime: "auto" | "cc" | "api" = appConfig.defaultRuntime,
): Promise<ChatResponse> {
  const body: Record<string, unknown> = { text, source, runtime, attachments }
  const res = await fetch(`${getBase()}/favilla/chat/send`, {
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

export type StreamChatEvent =
  | { event: "start"; data: { runtime?: string } }
  | { event: "tool_use"; data: { tool_use_id?: string; tool_name?: string; input_summary?: string } }
  | { event: "tool_result"; data: { tool_use_id?: string; tool_name?: string; result_summary?: string; is_error?: boolean } }
  | { event: "thought"; data: { index: number; text: string; source?: "marker" | "native" | "official" | "fiam"; locked?: boolean; summary?: string; icon?: string } }
  | { event: "thought_summary"; data: { index: number; summary?: string; icon?: string } }
  | { event: "text_delta"; data: { index: number; text: string } }
  | { event: "done"; data: ChatResponse }
  | { event: "error"; data: { message: string } }

export async function sendChatStream(
  text: string,
  source: string,
  attachments: ChatAttachment[],
  runtime: "auto" | "cc" | "api",
  onEvent: (ev: StreamChatEvent) => void,
  externalSignal?: AbortSignal,
): Promise<void> {
  const body: Record<string, unknown> = {
    text,
    source,
    runtime,
    attachments,
    request_id: `favilla-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    client_sent_at: Date.now() / 1000,
  }
  const headers = { ...authHeaders(), Accept: "text/event-stream" }
  // Two-phase abort:
  //  1) Initial fetch must complete within INITIAL_TIMEOUT_MS or we bail.
  //  2) Once streaming, every chunk resets the idle watchdog. If no chunk
  //     arrives for IDLE_TIMEOUT_MS the SSE is considered stalled and aborted.
  // Without these, a flaky uplink leaves the request hanging on OS-level
  // socket timeouts (often 1-3 minutes) with zero UI feedback.
  const INITIAL_TIMEOUT_MS = 30_000
  const IDLE_TIMEOUT_MS = 90_000
  const controller = new AbortController()
  let abortedExternally = false
  if (externalSignal) {
    const onAbort = () => { abortedExternally = true; try { controller.abort() } catch { /* ignore */ } }
    if (externalSignal.aborted) { onAbort() }
    else { externalSignal.addEventListener("abort", onAbort, { once: true }) }
  }
  let stalled = false
  let timedOutInitial = false
  const initialTimer: ReturnType<typeof setTimeout> = setTimeout(() => {
    timedOutInitial = true
    try { controller.abort() } catch { /* ignore */ }
  }, INITIAL_TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${getBase()}/favilla/chat/send`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    })
  } catch (e) {
    clearTimeout(initialTimer)
    if (abortedExternally) return
    const msg = timedOutInitial
      ? `connect timeout after ${Math.round(INITIAL_TIMEOUT_MS / 1000)}s`
      : (e instanceof Error ? e.message : String(e))
    onEvent({ event: "error", data: { message: msg } })
    return
  }
  clearTimeout(initialTimer)
  if (!res.ok || !res.body) {
    let errMsg = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data && typeof data.error === "string") errMsg = data.error
    } catch { /* ignore */ }
    onEvent({ event: "error", data: { message: errMsg } })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder("utf-8")
  let buf = ""
  let curEvent = "message"
  let curData = ""
  let idleTimer: ReturnType<typeof setTimeout> | null = null
  const armIdle = () => {
    if (idleTimer) clearTimeout(idleTimer)
    idleTimer = setTimeout(() => {
      stalled = true
      try { controller.abort() } catch { /* ignore */ }
      try { reader.cancel() } catch { /* ignore */ }
    }, IDLE_TIMEOUT_MS)
  }
  const disarmIdle = () => {
    if (idleTimer) { clearTimeout(idleTimer); idleTimer = null }
  }
  armIdle()
  const flush = () => {
    if (!curData) { curEvent = "message"; return }
    let parsed: unknown = {}
    try { parsed = JSON.parse(curData) } catch { /* ignore */ }
    onEvent({ event: curEvent as StreamChatEvent["event"], data: parsed as never })
    curEvent = "message"
    curData = ""
  }
  try {
    while (true) {
      let chunk: ReadableStreamReadResult<Uint8Array>
      try {
        chunk = await reader.read()
      } catch (e) {
        if (abortedExternally) return
        if (stalled) {
          onEvent({ event: "error", data: { message: `stream stalled (no data for ${Math.round(IDLE_TIMEOUT_MS / 1000)}s)` } })
        } else {
          const msg = e instanceof Error ? e.message : String(e)
          onEvent({ event: "error", data: { message: `stream error: ${msg}` } })
        }
        return
      }
      const { value, done } = chunk
      if (done) break
      armIdle() // any chunk resets the watchdog
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).replace(/\r$/, "")
        buf = buf.slice(idx + 1)
        if (line === "") {
          flush()
          continue
        }
        if (line.startsWith(":")) continue // SSE comment / heartbeat
        if (line.startsWith("event:")) {
          curEvent = line.slice(6).trim() || "message"
        } else if (line.startsWith("data:")) {
          const part = line.slice(5).replace(/^ /, "")
          curData = curData ? `${curData}\n${part}` : part
        } // ignore id: lines
      }
    }
    if (curData) flush()
  } finally {
    disarmIdle()
  }
}

export async function translateText(text: string): Promise<{ translated: string; source_lang: string; target_lang: string; error?: string }> {
  try {
    const res = await fetch(`${getBase()}/favilla/chat/translate`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ text }),
    })
    const data = await res.json()
    if (!res.ok) return { translated: "", source_lang: "", target_lang: "", error: data.error || `HTTP ${res.status}` }
    return data
  } catch (e) {
    return { translated: "", source_lang: "", target_lang: "", error: e instanceof Error ? e.message : "fetch failed" }
  }
}

export async function abortChat(channel = "chat"): Promise<{ aborted: boolean; reason?: string }> {
  try {
    const res = await fetch(`${getBase()}/favilla/chat/abort`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ channel }),
    })
    return await res.json()
  } catch {
    return { aborted: false, reason: "fetch failed" }
  }
}

export async function downloadObject(token: string, fallbackName = "attachment") {
  const clean = token.trim()
  if (!clean) throw new Error("missing object token")
  const res = await fetch(`${getBase()}/favilla/object/${encodeURIComponent(clean)}`, {
    method: "GET",
    headers: authHeaders(),
  })
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data && typeof data.error === "string") message = data.error
    } catch { /* ignore */ }
    throw new Error(message)
  }
  const blob = await res.blob()
  const disposition = res.headers.get("Content-Disposition") || ""
  const match = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i)
  const name = decodeURIComponent((match?.[1] || match?.[2] || fallbackName || "attachment").trim())
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function fetchObjectBlob(token: string): Promise<Blob> {
  const clean = token.trim()
  if (!clean) throw new Error("missing object token")
  const res = await fetch(`${getBase()}/favilla/object/${encodeURIComponent(clean)}`, {
    method: "GET",
    headers: authHeaders(),
  })
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data && typeof data.error === "string") message = data.error
    } catch { /* ignore */ }
    throw new Error(message)
  }
  return res.blob()
}

export async function sendStrollMessage(
  text: string,
  context: StrollSpatialContext,
  attachments: ChatAttachment[] = [],
  runtime: "auto" | "cc" | "api" = appConfig.defaultRuntime,
): Promise<ChatResponse> {
  const body: Record<string, unknown> = { text, source: "stroll", runtime, attachments, stroll_context: context }
  const res = await fetch(`${getBase()}/favilla/stroll/send`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, reply: "", error: data.error || `HTTP ${res.status}` }
  return data as ChatResponse
}

export type StickerEntry = { hash: string; name: string; tags?: string[]; added_by?: string; added_at?: string }

export async function fetchStickers(): Promise<{ ok: boolean; stickers?: StickerEntry[]; error?: string }> {
  const res = await fetch(`${getBase()}/favilla/stickers`, { headers: authHeaders() })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as { ok: boolean; stickers: StickerEntry[] }
}

export async function transcribeAudioServer(audioBlob: Blob): Promise<{ ok: boolean; text?: string; error?: string }> {
  const reader = new FileReader()
  const b64 = await new Promise<string>((resolve, reject) => {
    reader.onload = () => {
      const result = reader.result as string
      resolve(result.split(",")[1] || "")
    }
    reader.onerror = reject
    reader.readAsDataURL(audioBlob)
  })
  const res = await fetch(`${getBase()}/favilla/stt`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ audio: b64 }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return { ok: true, text: data.text || "" }
}

export async function fetchChatTranscript(source = "chat", limit = 300): Promise<TranscriptResponse> {
  const params = new URLSearchParams({ source, limit: String(limit) })
  const res = await fetch(`${getBase()}/favilla/chat/transcript?${params.toString()}`, {
    method: "GET",
    headers: authHeaders(),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as TranscriptResponse
}

export async function fetchStrollTranscript(limit = 300): Promise<TranscriptResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  const res = await fetch(`${getBase()}/favilla/stroll/transcript?${params.toString()}`, {
    method: "GET",
    headers: authHeaders(),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as TranscriptResponse
}

export async function fetchStrollNearby(current: Pick<StrollTrackPoint, "lng" | "lat">, radiusM = 50, changedSince = 0): Promise<StrollNearbyResponse> {
  const params = new URLSearchParams({ lng: String(current.lng), lat: String(current.lat), radiusM: String(radiusM), changedSince: String(changedSince) })
  const res = await fetch(`${getBase()}/favilla/stroll/nearby?${params.toString()}`, {
    method: "GET",
    headers: authHeaders(),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as StrollNearbyResponse
}

export async function writeStrollRecord(record: Omit<Partial<StrollSpatialRecord>, "createdAt" | "updatedAt"> & Pick<StrollSpatialRecord, "lng" | "lat" | "kind" | "origin">): Promise<StrollRecordResponse> {
  const res = await fetch(`${getBase()}/favilla/stroll/records`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(record),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as StrollRecordResponse
}

export async function reportStrollActionResult(payload: Record<string, unknown>) {
  const res = await fetch(`${getBase()}/favilla/stroll/action-result`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as { ok: boolean; action?: unknown; error?: string }
}

export async function fetchDashboardSummary(): Promise<DashboardSummary> {
  try {
    const res = await fetch(`${getBase()}/favilla/dashboard`, {
      method: "GET",
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
    return data as DashboardSummary
  } catch (e) {
    return { ok: false, error: String(e) }
  }
}

export async function fetchStudioState(): Promise<StudioStateResponse> {
  try {
    const res = await fetch(`${getBase()}/favilla/studio`, {
      method: "GET",
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
    return data as StudioStateResponse
  } catch (e) {
    return { ok: false, error: String(e) }
  }
}

export async function saveStudioState(state: StudioWorkspaceState): Promise<StudioStateResponse> {
  try {
    const res = await fetch(`${getBase()}/favilla/studio`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ state }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
    return data as StudioStateResponse
  } catch (e) {
    return { ok: false, error: String(e) }
  }
}

export async function requestStudioEdit(payload: StudioEditRequest): Promise<StudioEditResponse> {
  try {
    const res = await fetch(`${getBase()}/favilla/studio/edit`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
    return data as StudioEditResponse
  } catch (e) {
    return { ok: false, error: String(e) }
  }
}

export async function recordChatMessage(message: Omit<StoredChatMessage, "id" | "t">, source = "chat") {
  const res = await fetch(`${getBase()}/favilla/chat/transcript`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ source, ...message }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
  return data as { ok: boolean; message?: StoredChatMessage; error?: string }
}

// --- Memory operations (manual mode) ---
//
// recallNow:  user-triggered recall refresh. Server writes recall.md so the
//             AI sees relevant past events on the NEXT message. Does not chat.
// sealEvent:  user-triggered manual cut. Server packages everything since the
//             previous cut into one event (compute embedding, write fingerprint,
//             ask DS for graph edges, name it). Async-heavy; UI fires & forgets.
//
// The server records both API and CC Chat turns into flow/features, so
// process/recall can see turns across runtime switches.

export type MemoryOpResponse = { ok: boolean; error?: string; [k: string]: unknown }

async function postMemoryOp(path: string, body: object = {}): Promise<MemoryOpResponse> {
  try {
    const res = await fetch(`${getBase()}${path}`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ source: "chat", ...body }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: data.error || `HTTP ${res.status}` }
    return { ok: true, ...data }
  } catch (e) {
    return { ok: false, error: String(e) }
  }
}

export function recallNow() {
  return postMemoryOp("/favilla/chat/recall")
}

// cutFlow: drop a divider marker into the unprocessed flow. The next
//          processFlow() call will use these markers to split beats into
//          multiple events. Cutting alone does NOT trigger DS work.
export function cutFlow() {
  return postMemoryOp("/favilla/chat/cut")
}

// processFlow: ask the server to seal all unprocessed beats into events
//              (using cut markers as segment dividers). Synchronous —
//              resolves only when DS is done. UI should disable Send and
//              show the hourglass animation while this is in flight.
export function processFlow() {
  return postMemoryOp("/favilla/chat/process")
}

// Back-compat alias.
export function sealEvent() {
  return postMemoryOp("/favilla/chat/process")
}
