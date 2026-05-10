export type LimenCapability = "camera.snapshot" | "camera.mjpeg" | "screen.text"

export type LimenHealthResponse = {
  ok: boolean
  device_id: string
  role: string
  ip: string
  rssi?: number
  capabilities?: Array<LimenCapability | string>
  endpoints?: {
    health?: string
    stream?: string
    capture?: string
    screen?: string
  }
}

export type LimenScreenContent = {
  type?: "message" | "word" | "kaomoji" | "face" | "emoji" | "anim" | "status"
  text: string
  emoji?: string
}

export function normalizeLimenBaseUrl(value: string) {
  const trimmed = value.trim().replace(/\/+$/, "")
  if (!trimmed) return ""
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return `http://${trimmed}`
}

export function limenUrl(baseUrl: string, path: string) {
  const base = normalizeLimenBaseUrl(baseUrl)
  if (!base) throw new Error("Missing Limen URL")
  return `${base}${path.startsWith("/") ? path : `/${path}`}`
}

export function limenStreamUrl(baseUrl: string) {
  return limenUrl(baseUrl, "/stream")
}

export async function fetchLimenHealth(baseUrl: string, signal?: AbortSignal): Promise<LimenHealthResponse> {
  const response = await fetch(limenUrl(baseUrl, "/health"), { cache: "no-store", signal })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `Limen health HTTP ${response.status}`)
  return data as LimenHealthResponse
}

export async function captureLimenPhoto(baseUrl: string, signal?: AbortSignal): Promise<File> {
  const response = await fetch(limenUrl(baseUrl, "/capture"), { cache: "no-store", signal })
  if (!response.ok) throw new Error(`Limen capture HTTP ${response.status}`)
  const blob = await response.blob()
  return new File([blob], timestampedLimenPhotoName(), { type: blob.type || "image/jpeg" })
}

export async function sendLimenScreenText(baseUrl: string, content: LimenScreenContent, signal?: AbortSignal) {
  const text = formatLimenScreenText(content)
  const response = await fetch(limenUrl(baseUrl, "/screen"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
    signal,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `Limen screen HTTP ${response.status}`)
  return data as { ok: boolean; shown?: string; error?: string }
}

export function formatLimenScreenText(content: LimenScreenContent) {
  const rawText = normalizeVisibleText(content.text)
  const type = content.type || inferScreenType(rawText, content.emoji)
  const text = trimDisplayUnits(rawText || content.emoji || "ready", limitForType(type))
  if (type === "message") return text
  return `${type}:${text}`
}

export function displayLimenScreenText(content: LimenScreenContent) {
  return trimDisplayUnits(normalizeVisibleText(content.text) || content.emoji || "ready", 32)
}

function timestampedLimenPhotoName(date = new Date()) {
  const pad = (value: number) => value.toString().padStart(2, "0")
  return `limen-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}.jpg`
}

function inferScreenType(text: string, emoji?: string): NonNullable<LimenScreenContent["type"]> {
  if (!text && emoji) return "emoji"
  if (/^[a-z0-9][a-z0-9 -]{0,13}$/i.test(text)) return "word"
  if (looksLikeKaomoji(text)) return "kaomoji"
  return "message"
}

function limitForType(type: NonNullable<LimenScreenContent["type"]>) {
  if (type === "message") return 72
  if (type === "status") return 32
  return 14
}

function normalizeVisibleText(text: string) {
  return text.replace(/\s+/g, " ").trim()
}

function trimDisplayUnits(text: string, maxUnits: number) {
  let used = 0
  let out = ""
  for (const char of Array.from(text)) {
    const units = char.charCodeAt(0) > 0x7f ? 2 : 1
    if (used + units > maxUnits) break
    used += units
    out += char
  }
  return out.trimEnd()
}

function looksLikeKaomoji(text: string) {
  if (text.length > 14) return false
  return /[()（）^._;:<>'`~\-]/.test(text) && !/[a-z]{4,}/i.test(text)
}