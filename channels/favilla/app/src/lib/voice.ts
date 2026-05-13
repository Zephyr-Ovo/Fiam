import { appConfig } from "../config"

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
