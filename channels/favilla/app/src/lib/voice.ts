import { appConfig } from "../config"

// --- Autoplay / Android WebView unlock --------------------------------------
// HTMLAudioElement.play() is rejected with NotAllowedError unless it runs in
// (or after) a real user gesture. Two paths hit this: auto-playing an AI reply
// (no gesture at all) and a *manual* tap whose play() happens after an awaited
// TTS fetch (the gesture activation is gone by then). Fix: keep ONE reusable
// audio element and "unlock" it on the first user interaction; afterwards all
// programmatic play() on that same element is allowed for the session.
let _sharedAudio: HTMLAudioElement | null = null
let _audioUnlocked = false
const _SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA="

export function getSharedAudio(): HTMLAudioElement {
  if (!_sharedAudio) _sharedAudio = new Audio()
  return _sharedAudio
}

export function unlockAudio(): void {
  if (_audioUnlocked || typeof Audio === "undefined") return
  const a = getSharedAudio()
  try {
    a.muted = true
    a.src = _SILENT_WAV
    const p = a.play() as unknown as Promise<void> | undefined
    const done = () => {
      try { a.pause(); a.currentTime = 0 } catch { /* */ }
      a.muted = false
      _audioUnlocked = true
    }
    if (p && typeof p.then === "function") p.then(done).catch(() => { a.muted = false })
    else done()
  } catch {
    a.muted = false
  }
}

if (typeof window !== "undefined") {
  const onGesture = () => unlockAudio()
  window.addEventListener("pointerdown", onGesture, { passive: true })
  window.addEventListener("touchend", onGesture, { passive: true })
  window.addEventListener("keydown", onGesture)
}

export type VoicePlaybackProvider = "browser" | "openai_compatible" | "mimo"
export type VoiceSttProvider = "browser" | "openai_compatible"

type BrowserSpeechRecognitionCtor = new () => BrowserSpeechRecognition

interface BrowserSpeechRecognition {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
}

interface BrowserSpeechRecognitionAlternative {
  transcript: string
}

interface BrowserSpeechRecognitionResult {
  readonly isFinal: boolean
  readonly length: number
  [index: number]: BrowserSpeechRecognitionAlternative
}

interface BrowserSpeechRecognitionEvent {
  readonly resultIndex: number
  readonly results: {
    readonly length: number
    [index: number]: BrowserSpeechRecognitionResult
  }
}

declare global {
  interface Window {
    webkitSpeechRecognition?: BrowserSpeechRecognitionCtor
    SpeechRecognition?: BrowserSpeechRecognitionCtor
  }
}

function trimBase(url: string): string {
  return String(url || "").trim().replace(/\/+$/, "")
}

function authHeaders(apiKey: string): Record<string, string> {
  const headers: Record<string, string> = {}
  if (apiKey.trim()) headers.Authorization = `Bearer ${apiKey.trim()}`
  return headers
}

export function createBrowserSttSession(options: {
  lang?: string
  onFinalText: (text: string) => void
  onError?: (message: string) => void
  onEnd?: () => void
}) {
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!Ctor) return null
  const recognition = new Ctor()
  recognition.lang = options.lang || "zh-CN"
  recognition.continuous = false
  recognition.interimResults = true

  recognition.onresult = (event) => {
    let finalText = ""
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i]
      const alt = result[0]
      if (!alt?.transcript) continue
      if (result.isFinal) finalText += alt.transcript
    }
    if (finalText.trim()) options.onFinalText(finalText.trim())
  }

  recognition.onerror = (event) => {
    if (options.onError) options.onError(event.error || "speech recognition failed")
  }

  recognition.onend = () => {
    if (options.onEnd) options.onEnd()
  }

  return {
    start: () => recognition.start(),
    stop: () => recognition.stop(),
  }
}

export async function transcribeAudioOpenAICompatible(audio: Blob): Promise<string> {
  const baseUrl = trimBase(appConfig.sttBaseUrl)
  if (!baseUrl) throw new Error("STT base URL is empty")
  const form = new FormData()
  form.append("model", appConfig.sttModel || "whisper-1")
  form.append("file", new File([audio], "favilla-stt.webm", { type: audio.type || "audio/webm" }))

  const response = await fetch(`${baseUrl}/audio/transcriptions`, {
    method: "POST",
    headers: authHeaders(appConfig.sttApiKey),
    body: form,
  })
  if (!response.ok) {
    const text = await response.text().catch(() => "")
    throw new Error(`STT ${response.status}: ${text || "request failed"}`)
  }
  const payload = (await response.json()) as { text?: string }
  return String(payload.text || "").trim()
}

export async function speakText(text: string): Promise<void> {
  const clean = String(text || "").trim()
  if (!clean) return

  const provider = (appConfig.ttsProvider || "browser") as VoicePlaybackProvider
  if (provider === "browser") {
    if (typeof window.speechSynthesis === "undefined") return
    const utterance = new SpeechSynthesisUtterance(clean)
    utterance.lang = "zh-CN"
    if (appConfig.ttsVoice) {
      const voice = window
        .speechSynthesis
        .getVoices()
        .find((item) => item.name === appConfig.ttsVoice || item.voiceURI === appConfig.ttsVoice)
      if (voice) utterance.voice = voice
    }
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
    return
  }

  const baseUrl = trimBase(appConfig.ttsBaseUrl)
  if (!baseUrl) throw new Error("TTS base URL is empty")

  let response: Response
  if (provider === "openai_compatible") {
    response = await fetch(`${baseUrl}/audio/speech`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(appConfig.ttsApiKey),
      },
      body: JSON.stringify({
        model: appConfig.ttsModel || "gpt-4o-mini-tts",
        voice: appConfig.ttsVoice || "alloy",
        input: clean,
        format: "mp3",
      }),
    })
  } else {
    response = await fetch(`${baseUrl}/tts`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(appConfig.ttsApiKey),
      },
      body: JSON.stringify({
        text: clean,
        voice: appConfig.ttsVoice,
        model: appConfig.ttsModel,
        format: "mp3",
      }),
    })
  }

  if (!response.ok) {
    const err = await response.text().catch(() => "")
    throw new Error(`TTS ${response.status}: ${err || "request failed"}`)
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  try {
    const audio = new Audio(url)
    await audio.play()
  } finally {
    URL.revokeObjectURL(url)
  }
}


export type TtsPlayerState = {
  status: "idle" | "loading" | "playing" | "paused" | "error"
  duration: number
  currentTime: number
  sourceId?: string
  error?: string
}

export type TtsPlayerListener = (state: TtsPlayerState) => void

export class TtsPlayer {
  private audio: HTMLAudioElement | null = null
  private objectUrl: string | null = null
  private listeners = new Set<TtsPlayerListener>()
  private _state: TtsPlayerState = { status: "idle", duration: 0, currentTime: 0 }
  private rafId: number | null = null

  get state() { return this._state }

  subscribe(fn: TtsPlayerListener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private emit(patch: Partial<TtsPlayerState>) {
    this._state = { ...this._state, ...patch }
    this.listeners.forEach((fn) => fn(this._state))
  }

  private startTick() {
    this.stopTick()
    const tick = () => {
      if (this.audio && this._state.status === "playing") {
        this.emit({ currentTime: this.audio.currentTime, duration: this.audio.duration || 0 })
      }
      this.rafId = requestAnimationFrame(tick)
    }
    this.rafId = requestAnimationFrame(tick)
  }

  private stopTick() {
    if (this.rafId !== null) { cancelAnimationFrame(this.rafId); this.rafId = null }
  }

  private attachAudioHandlers(sourceId?: string) {
    if (!this.audio) return
    this.audio.onended = () => { this.emit({ status: "idle", currentTime: 0, sourceId }); this.stopTick() }
    this.audio.onerror = () => { this.emit({ status: "error", error: "playback failed", sourceId }); this.stopTick() }
    this.audio.onloadedmetadata = () => { this.emit({ duration: this.audio!.duration || 0, sourceId }) }
  }

  private async playPreparedAudio(sourceId?: string): Promise<void> {
    if (!this.audio) return
    this.attachAudioHandlers(sourceId)
    await this.audio.play()
    this.emit({ status: "playing", sourceId })
    this.startTick()
  }

  async playUrl(url: string, sourceId?: string): Promise<void> {
    this.stop()
    this.emit({ status: "loading", duration: 0, currentTime: 0, sourceId })
    try {
      this.audio = getSharedAudio()
      this.audio.src = url
      await this.playPreparedAudio(sourceId)
    } catch (e) {
      this.emit({ status: "error", error: e instanceof Error ? e.message : String(e), sourceId })
    }
  }

  async playBlob(blob: Blob, sourceId?: string): Promise<void> {
    this.stop()
    this.emit({ status: "loading", duration: 0, currentTime: 0, sourceId })
    try {
      // Android WebView's <audio> will not decode a blob whose type is empty
      // or a non-audio MIME (e.g. application/octet-stream). The server now
      // sends a real audio MIME, but coerce defensively so a bad Content-Type
      // can never reproduce the silent / 0:00 failure.
      if (!blob.type || !blob.type.startsWith("audio/")) {
        blob = new Blob([blob], { type: "audio/mpeg" })
      }
      this.objectUrl = URL.createObjectURL(blob)
      this.audio = getSharedAudio()
      this.audio.src = this.objectUrl
      await this.playPreparedAudio(sourceId)
    } catch (e) {
      this.emit({ status: "error", error: e instanceof Error ? e.message : String(e), sourceId })
    }
  }

  async playFetched(fetcher: () => Promise<Blob>, sourceId?: string): Promise<void> {
    this.stop()
    this.emit({ status: "loading", duration: 0, currentTime: 0, sourceId })
    try {
      const blob = await fetcher()
      await this.playBlob(blob, sourceId)
    } catch (e) {
      this.emit({ status: "error", error: e instanceof Error ? e.message : String(e), sourceId })
    }
  }

  async play(text: string, sourceId?: string): Promise<void> {
    this.stop()
    const clean = String(text || "").trim()
    if (!clean) return

    const provider = (appConfig.ttsProvider || "browser") as VoicePlaybackProvider
    if (provider === "browser") {
      this.playBrowser(clean, sourceId)
      return
    }

    this.emit({ status: "loading", duration: 0, currentTime: 0, sourceId })
    try {
      const blob = await fetchTtsAudio(clean, provider)
      await this.playBlob(blob, sourceId)
    } catch (e) {
      this.emit({ status: "error", error: e instanceof Error ? e.message : String(e), sourceId })
    }
  }

  private playBrowser(text: string, sourceId?: string) {
    if (typeof window.speechSynthesis === "undefined") {
      this.emit({ status: "error", error: "speech synthesis unavailable", sourceId })
      return
    }
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = "zh-CN"
    if (appConfig.ttsVoice) {
      const v = window.speechSynthesis.getVoices().find((i) => i.name === appConfig.ttsVoice || i.voiceURI === appConfig.ttsVoice)
      if (v) utterance.voice = v
    }
    utterance.onstart = () => this.emit({ status: "playing", sourceId })
    utterance.onend = () => this.emit({ status: "idle", currentTime: 0, sourceId })
    utterance.onerror = () => this.emit({ status: "error", error: "speech synthesis error", sourceId })
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
    this.emit({ status: "loading", sourceId })
  }

  pause() {
    if (this.audio && this._state.status === "playing") {
      this.audio.pause()
      this.emit({ status: "paused" })
      this.stopTick()
    } else if (this._state.status === "playing" && typeof window.speechSynthesis !== "undefined") {
      window.speechSynthesis.pause()
      this.emit({ status: "paused" })
    }
  }

  resume() {
    if (this.audio && this._state.status === "paused") {
      void this.audio.play()
      this.emit({ status: "playing" })
      this.startTick()
    } else if (this._state.status === "paused" && typeof window.speechSynthesis !== "undefined") {
      window.speechSynthesis.resume()
      this.emit({ status: "playing" })
    }
  }

  stop() {
    this.stopTick()
    if (this.audio) {
      this.audio.pause()
      this.audio.onended = null
      this.audio.onerror = null
      this.audio = null
    }
    if (this.objectUrl) { URL.revokeObjectURL(this.objectUrl); this.objectUrl = null }
    window.speechSynthesis?.cancel()
    this.emit({ status: "idle", duration: 0, currentTime: 0, sourceId: undefined })
  }
}

async function fetchTtsAudio(text: string, provider: VoicePlaybackProvider): Promise<Blob> {
  const baseUrl = trimBase(appConfig.ttsBaseUrl)
  if (!baseUrl) throw new Error("TTS base URL is empty")
  let response: Response
  if (provider === "openai_compatible") {
    response = await fetch(`${baseUrl}/audio/speech`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(appConfig.ttsApiKey) },
      body: JSON.stringify({ model: appConfig.ttsModel || "gpt-4o-mini-tts", voice: appConfig.ttsVoice || "alloy", input: text, format: "mp3" }),
    })
  } else {
    response = await fetch(`${baseUrl}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(appConfig.ttsApiKey) },
      body: JSON.stringify({ text, voice: appConfig.ttsVoice, model: appConfig.ttsModel, format: "mp3" }),
    })
  }
  if (!response.ok) {
    const err = await response.text().catch(() => "")
    throw new Error(`TTS ${response.status}: ${err || "request failed"}`)
  }
  return response.blob()
}
