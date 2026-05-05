import { Camera, ChevronUp, Maximize2, Minimize2, Pause, Phone, Radio, Send, Square, Video } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { ConfirmModal } from "../components/ConfirmModal"
import { StrollMapView } from "@stroll-map/StrollMapView"
import { buildPhotoAnnotations, createAiEmojiAnnotation } from "@stroll-map/annotations"
import { sampleTrack } from "@stroll-map/sampleTrack"
import type { StrollMapAnnotation, StrollMapLabel, WeatherSnapshot } from "@stroll-map/types"
import { fetchWeatherSnapshot } from "@stroll-map/weather"

const INK = "#26304A"
const PAPER = "#DECBCD"
const PEACH = "#D99D92"
const WATER = "#88AFAF"
const BLUE = "#576E91"
const GLASS = "rgba(38,48,74,0.40)"
const GLASS_BORDER = "rgba(255,250,243,0.24)"
const MAPBOX_TOKEN = (import.meta.env.VITE_MAPBOX_TOKEN as string | undefined)?.trim() ?? ""
const WEATHER_ENDPOINT = (import.meta.env.VITE_STROLL_WEATHER_ENDPOINT as string | undefined)?.trim()
const CALL_RECORDINGS_STORAGE_KEY = "favilla:stroll-call-recordings:v1"

const LAYOUT = {
  cameraTop: 0,
  cameraInset: 12,
  cameraRadius: 4,
  bridgeTop: -40,
  bridgeHeight: 64,
  xiaoX: 27,
  xiaoY: -10,
  xiaoSize: 74,
  xiaoInner: 56,
  switchRight: 20,
  switchTop: 42,
  switchWidth: 118,
  switchHeight: 32,
  mapTop: -5,
  mapFade: 0,
  chatX: 22,
  chatBottom: 112,
  chatWidth: 258,
  bubbleRadius: 14,
  composerBottom: 0,
  composerInset: 18,
  composerHeight: 36,
  composerGap: 8,
  callWidth: 36,
  callRecordWidth: 92,
  foldWidth: 42,
  expandButtonInset: 12,
} as const

type CallRecordingState = "idle" | "recording" | "paused"

type Props = {
  onBack: () => void
  active: boolean
}

const chatter = [
  { name: "Fiet", text: "light changed at the next crossing" },
  { name: "you", text: "keep live, no photo yet" },
  { name: "Fiet", text: "xiao is showing the walking face" },
]

const mapLabels: StrollMapLabel[] = [
  { id: "turn", lng: sampleTrack[3].lng, lat: sampleTrack[3].lat, text: "we turned here", tone: "start" },
  { id: "flower", lng: sampleTrack[8].lng, lat: sampleTrack[8].lat, text: "flower photo", tone: "note" },
  { id: "slick", lng: sampleTrack[12].lng, lat: sampleTrack[12].lat, text: "slick paving", tone: "current" },
]

const mapAnnotations: StrollMapAnnotation[] = [
  ...buildPhotoAnnotations([
    { id: "flower-a", lng: sampleTrack[8].lng, lat: sampleTrack[8].lat, takenAt: Date.now(), source: "phone" },
    { id: "flower-b", lng: sampleTrack[8].lng + 0.00008, lat: sampleTrack[8].lat + 0.00004, takenAt: Date.now(), source: "phone" },
  ]),
  createAiEmojiAnnotation({ id: "ai-slick", lng: sampleTrack[12].lng, lat: sampleTrack[12].lat, emoji: "⚠️", text: "slick paving" }),
]

export function Stroll({ onBack, active }: Props) {
  const [confirmEnd, setConfirmEnd] = useState(false)
  const [callActive, setCallActive] = useState(false)
  const [callRecordingState, setCallRecordingState] = useState<CallRecordingState>("idle")
  const [callRecordingSeconds, setCallRecordingSeconds] = useState(0)
  const [savedCallRecording, setSavedCallRecording] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const [mediaMode, setMediaMode] = useState<"live" | "photo">("live")
  const [conversationOpen, setConversationOpen] = useState(true)
  const [conversationLines, setConversationLines] = useState(chatter)
  const [screenExpanded, setScreenExpanded] = useState(false)
  const [mapExpanded, setMapExpanded] = useState(false)
  const [weather, setWeather] = useState<WeatherSnapshot>({ kind: "clear", intensity: 0.24, source: "fallback" })
  const foldStartYRef = useRef<number | null>(null)
  const wasActiveRef = useRef(active)

  useEffect(() => {
    const controller = new AbortController()
    fetchWeatherSnapshot(sampleTrack[sampleTrack.length - 1], WEATHER_ENDPOINT, controller.signal)
      .then(setWeather)
      .catch(() => setWeather({ kind: "clear", intensity: 0.24, source: "fallback" }))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (wasActiveRef.current && !active) resetStrollSession()
    wasActiveRef.current = active
  }, [active])

  useEffect(() => {
    if (callRecordingState !== "recording") return
    const timer = window.setInterval(() => setCallRecordingSeconds((seconds) => seconds + 1), 1000)
    return () => window.clearInterval(timer)
  }, [callRecordingState])

  function startCall() {
    setCallActive(true)
    setCallRecordingState("idle")
    setCallRecordingSeconds(0)
    setSavedCallRecording(null)
  }

  function endCall() {
    setCallActive(false)
    setCallRecordingState("idle")
    setCallRecordingSeconds(0)
  }

  function startCallRecording() {
    setSavedCallRecording(null)
    setCallRecordingSeconds(0)
    setCallRecordingState("recording")
  }

  function toggleCallRecordingPause() {
    setCallRecordingState((state) => (state === "recording" ? "paused" : "recording"))
  }

  function stopCallRecording() {
    const durationSeconds = Math.max(callRecordingSeconds, 1)
    const fileName = timestampedRecordingName()
    saveCallRecordingMetadata(fileName, durationSeconds)
    setSavedCallRecording(fileName)
    setCallRecordingState("idle")
    setCallRecordingSeconds(0)
  }

  function resetStrollSession() {
    setConversationLines([])
    setConversationOpen(true)
    setScreenExpanded(false)
    setMapExpanded(false)
    setCallActive(false)
    setCallRecordingState("idle")
    setCallRecordingSeconds(0)
    setSavedCallRecording(null)
    setRecording(false)
  }

  function closeStroll() {
    resetStrollSession()
    onBack()
  }

  function foldHome() {
    foldStartYRef.current = null
    closeStroll()
  }

  function onFoldPointerMove(e: React.PointerEvent<HTMLButtonElement>) {
    if (foldStartYRef.current === null) return
    if (foldStartYRef.current - e.clientY > 24) foldHome()
  }

  return (
    <div
      className="relative flex h-full w-full flex-col overflow-hidden"
      style={{ background: `linear-gradient(180deg, ${PAPER} 0%, #B9B9BE 46%, #90A8B6 100%)` }}
    >
      <WeatherCurtain weather={weather} />
      <header className="relative z-30 flex items-center justify-between px-4 pb-2 pt-[calc(env(safe-area-inset-top)+13px)]">
        <button
          type="button"
          onClick={() => setConfirmEnd(true)}
          className="grid h-8 w-8 place-items-center border-0 bg-transparent p-0"
          style={{ color: INK }}
          aria-label="End Stroll"
        >
          <ExitStrollIcon />
        </button>
        <div className="flex flex-col items-center leading-none">
          <span className="text-[19px] font-semibold italic" style={{ color: INK, fontFamily: "var(--font-serif)" }}>
            Stroll
          </span>
        </div>
        <div className="h-8 w-8" aria-hidden="true" />
      </header>

      <section
        className={screenExpanded ? "absolute" : "relative z-10"}
        style={screenExpanded
          ? {
              inset: 0,
              zIndex: 18,
              paddingLeft: LAYOUT.cameraInset,
              paddingRight: LAYOUT.cameraInset,
              paddingTop: "calc(env(safe-area-inset-top) + 54px)",
              paddingBottom: "calc(env(safe-area-inset-bottom) + 80px)",
              opacity: mapExpanded ? 0 : 1,
              pointerEvents: mapExpanded ? "none" : "auto",
            }
          : { marginTop: LAYOUT.cameraTop, paddingLeft: LAYOUT.cameraInset, paddingRight: LAYOUT.cameraInset, opacity: mapExpanded ? 0 : 1, pointerEvents: mapExpanded ? "none" : "auto" }}
      >
        <div
          className={`relative w-full overflow-hidden ${screenExpanded ? "h-full" : "aspect-[4/3]"}`}
          style={{
            borderRadius: LAYOUT.cameraRadius,
            background:
              "linear-gradient(135deg, rgba(38,48,74,0.96), rgba(87,110,145,0.68) 52%, rgba(136,175,175,0.66))",
            boxShadow: "0 18px 42px -24px rgba(38,48,74,0.72)",
          }}
        >
          <div className="absolute inset-0 opacity-35" style={{
            backgroundImage:
              "linear-gradient(90deg, rgba(255,255,255,0.12) 1px, transparent 1px), linear-gradient(0deg, rgba(255,255,255,0.12) 1px, transparent 1px)",
            backgroundSize: "34px 34px",
          }} />
          <div className="absolute left-4 top-4 flex items-center gap-2 rounded-full px-3 py-1.5" style={{ background: "rgba(1,3,1,0.36)", color: "#fff" }}>
            <Video className="h-3.5 w-3.5" strokeWidth={1.8} />
            <span className="text-[11px] font-medium uppercase tracking-[0.18em]">live</span>
          </div>
          <SurfaceExpandButton
            expanded={screenExpanded}
            label={screenExpanded ? "Shrink screen" : "Expand screen"}
            onClick={() => {
              setScreenExpanded((expanded) => !expanded)
              setMapExpanded(false)
            }}
          />
        </div>
      </section>

      <section className="relative z-20 px-4" style={{ height: LAYOUT.bridgeHeight, marginTop: LAYOUT.bridgeTop, opacity: mapExpanded || screenExpanded ? 0 : 1, pointerEvents: mapExpanded || screenExpanded ? "none" : "auto" }}>
        <div
          className="absolute grid place-items-center rounded-full"
          style={{
            left: LAYOUT.xiaoX,
            top: LAYOUT.xiaoY,
            width: LAYOUT.xiaoSize,
            height: LAYOUT.xiaoSize,
            background: `radial-gradient(circle at 38% 32%, #CBE8E9, ${WATER} 42%, ${BLUE} 100%)`,
            border: "3px solid rgba(255,255,255,0.72)",
            boxShadow: "0 14px 34px -14px rgba(1,3,1,0.72)",
          }}
          aria-label="Xiao screen preview"
        >
          <div className="rounded-full" style={{ width: LAYOUT.xiaoInner, height: LAYOUT.xiaoInner, background: "rgba(30,40,67,0.82)", boxShadow: `0 -9px 0 ${PEACH} inset` }} />
        </div>
        <div className="absolute" style={{ right: LAYOUT.switchRight, top: LAYOUT.switchTop }}>
          <ModeSwitch mode={mediaMode} width={LAYOUT.switchWidth} height={LAYOUT.switchHeight} onChange={() => setMediaMode((mode) => (mode === "live" ? "photo" : "live"))} />
        </div>
      </section>

      <section
        className="overflow-hidden"
        style={mapExpanded
          ? { position: "absolute", inset: 0, zIndex: 18, marginTop: 0 }
          : { position: "relative", minHeight: 0, flex: "1 1 0%", marginTop: LAYOUT.mapTop, opacity: screenExpanded ? 0 : 1, pointerEvents: screenExpanded ? "none" : "auto" }}
      >
        <MapPanel
          weather={weather}
          expanded={mapExpanded}
          onToggleExpand={() => {
            setMapExpanded((expanded) => !expanded)
            setScreenExpanded(false)
          }}
        />
        <div className="pointer-events-none absolute inset-x-0 top-0" style={{ height: LAYOUT.mapFade, background: "linear-gradient(180deg, rgba(225,212,204,0.88) 0%, rgba(225,212,204,0.38) 46%, transparent 100%)" }} />
      </section>

      <ConversationLayer lines={conversationLines} bottom={LAYOUT.chatBottom} open={conversationOpen} onHide={() => setConversationOpen(false)} onShow={() => setConversationOpen(true)} />

      <div className="absolute inset-x-0 z-30 pb-[calc(env(safe-area-inset-bottom)+10px)] pt-3" style={{ bottom: LAYOUT.composerBottom, background: "transparent" }}>
        <div className="flex items-center" style={{ height: LAYOUT.composerHeight, gap: LAYOUT.composerGap, paddingLeft: LAYOUT.composerInset, paddingRight: LAYOUT.composerInset }}>
          {callActive ? (
            <>
              <div className="flex min-w-0 flex-1 items-center gap-2 rounded-[12px] px-2.5" style={{ height: LAYOUT.composerHeight, background: "rgba(38,48,74,0.60)", border: `1px solid rgba(217,157,146,0.42)`, color: "rgba(255,250,243,0.9)", backdropFilter: "blur(10px)", fontFamily: "var(--font-sans)" }}>
                <span className="shrink-0 text-[12px] tabular-nums" style={{ color: "rgba(255,250,243,0.82)" }}>00:42</span>
                <WaveBars count={8} active />
                <button type="button" onClick={endCall} className="grid h-6 w-6 shrink-0 place-items-center rounded-[8px]" style={{ background: "rgba(166,75,79,0.9)", color: "#FFF7EF" }} aria-label="Hang up">
                  <Phone className="h-3.5 w-3.5 rotate-[135deg]" strokeWidth={2} />
                </button>
              </div>
              <CallRecordingControl
                state={callRecordingState}
                seconds={callRecordingSeconds}
                savedFileName={savedCallRecording}
                onRecord={startCallRecording}
                onPause={toggleCallRecordingPause}
                onStop={stopCallRecording}
              />
            </>
          ) : (
            <>
              <div className="flex min-w-0 flex-1 items-center gap-2 rounded-[12px] px-2.5" style={{ height: LAYOUT.composerHeight, background: recording ? "rgba(38,48,74,0.55)" : GLASS, border: `1px solid ${recording ? "rgba(217,157,146,0.46)" : GLASS_BORDER}`, color: "rgba(255,250,243,0.74)", backdropFilter: "blur(10px)" }}>
                <button
                  type="button"
                  className="grid h-6 w-6 shrink-0 place-items-center rounded-[8px]"
                  style={{ background: recording ? "rgba(217,157,146,0.86)" : "rgba(255,250,243,0.18)", color: recording ? INK : "rgba(255,250,243,0.86)" }}
                  aria-label="Hold to record voice"
                  onPointerDown={(e) => { setRecording(true); e.currentTarget.setPointerCapture?.(e.pointerId) }}
                  onPointerUp={() => setRecording(false)}
                  onPointerCancel={() => setRecording(false)}
                  onPointerLeave={() => setRecording(false)}
                >
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: recording ? INK : "rgba(255,250,243,0.9)", boxShadow: `0 0 0 3px ${recording ? "rgba(38,48,74,0.18)" : "rgba(255,250,243,0.16)"}` }} />
                </button>
                {recording ? (
                  <div className="flex min-w-0 flex-1 items-center gap-1" aria-label="Recording voice">
                    <WaveBars count={11} active />
                    <span className="ml-1 text-[12px]" style={{ color: "rgba(255,250,243,0.76)", fontFamily: "var(--font-sans)" }}>release</span>
                  </div>
                ) : (
                  <>
                    <input className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-[rgba(255,250,243,0.62)]" style={{ fontFamily: "var(--font-sans)" }} placeholder="Say…" />
                    <button type="button" className="grid h-6 w-6 place-items-center rounded-full" style={{ background: "rgba(244,214,204,0.92)", color: INK }} aria-label="Send stroll message">
                      <Send className="h-3.5 w-3.5" strokeWidth={1.8} />
                    </button>
                  </>
                )}
              </div>
              <button type="button" className="grid shrink-0 place-items-center rounded-[14px]" style={{ width: LAYOUT.callWidth, height: LAYOUT.composerHeight, background: "rgba(244,214,204,0.9)", color: INK, border: "1px solid rgba(255,250,243,0.32)", boxShadow: "0 8px 20px -16px rgba(1,3,1,0.72)" }} aria-label="Start call" onClick={startCall}>
                <Phone className="h-4 w-4" strokeWidth={1.9} />
              </button>
            </>
          )}
          <button
            type="button"
            className="grid shrink-0 place-items-center rounded-[12px] text-[14px] font-medium"
            style={{ width: LAYOUT.foldWidth, height: LAYOUT.composerHeight, background: "rgba(38,48,74,0.82)", color: "#F4D6CC", fontFamily: "var(--font-sans)" }}
            aria-label="Pause and fold Stroll"
            onPointerDown={(e) => {
              foldStartYRef.current = e.clientY
              e.currentTarget.setPointerCapture?.(e.pointerId)
            }}
            onPointerMove={onFoldPointerMove}
            onPointerUp={() => { foldStartYRef.current = null }}
            onPointerCancel={() => { foldStartYRef.current = null }}
            onClick={foldHome}
          >
            <ChevronUp className="h-5 w-5" strokeWidth={1.9} />
          </button>
        </div>
      </div>
      <ConfirmModal
        open={confirmEnd}
        title="End stroll?"
        message="This will end the live stroll session."
        cancelLabel="Cancel"
        confirmLabel="End"
        onCancel={() => setConfirmEnd(false)}
        onConfirm={() => { setConfirmEnd(false); closeStroll() }}
      />
    </div>
  )
}

function ExitStrollIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M4.5 10.5V12.5C4.5 12.7652 4.60536 13.0196 4.79289 13.2071C4.98043 13.3946 5.23478 13.5 5.5 13.5H12.5C12.7652 13.5 13.0196 13.3946 13.2071 13.2071C13.3946 13.0196 13.5 12.7652 13.5 12.5V1.5C13.5 1.23478 13.3946 0.98043 13.2071 0.792893C13.0196 0.605357 12.7652 0.5 12.5 0.5H5.5C5.23478 0.5 4.98043 0.605357 4.79289 0.792893C4.60536 0.98043 4.5 1.23478 4.5 1.5V3.5" stroke="#000001" strokeWidth={1.35} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M7.5 7H0.5" stroke="#000001" strokeWidth={1.35} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M2.5 5L0.5 7L2.5 9" stroke="#000001" strokeWidth={1.35} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function CallRecordingControl({ state, seconds, savedFileName, onRecord, onPause, onStop }: { state: CallRecordingState; seconds: number; savedFileName: string | null; onRecord: () => void; onPause: () => void; onStop: () => void }) {
  const isRecording = state !== "idle"
  return (
    <div className="flex shrink-0 items-center justify-center gap-1 rounded-[12px] px-1.5" style={{ width: isRecording ? LAYOUT.callRecordWidth : LAYOUT.callWidth, height: LAYOUT.composerHeight, background: isRecording ? GLASS : "transparent", border: isRecording ? `1px solid ${GLASS_BORDER}` : "1px solid transparent", color: isRecording ? "rgba(255,250,243,0.9)" : INK, backdropFilter: isRecording ? "blur(10px)" : undefined, fontFamily: "var(--font-sans)", boxShadow: savedFileName && !isRecording ? "0 0 0 2px rgba(217,157,146,0.26) inset" : undefined }} aria-label={isRecording ? "Call recording controls" : "Record call"}>
      {state === "idle" ? (
        <button type="button" className="grid h-8 w-8 place-items-center rounded-full border-0 bg-transparent p-0" aria-label="Record call" title={savedFileName ?? undefined} onClick={onRecord}>
          <span className="h-3.5 w-3.5 rounded-full" style={{ background: "#C74A4F", boxShadow: "0 0 0 4px rgba(199,74,79,0.16)" }} />
        </button>
      ) : (
        <>
          <button type="button" className="grid h-6 w-6 place-items-center rounded-[8px]" style={{ background: "rgba(255,250,243,0.14)" }} aria-label={state === "recording" ? "Pause call recording" : "Resume call recording"} onClick={onPause}>
            {state === "recording" ? <Pause className="h-3.5 w-3.5" strokeWidth={2} /> : <span className="ml-0.5 h-0 w-0 border-y-[5px] border-l-[8px] border-y-transparent border-l-[#26304A]" />}
          </button>
          <button type="button" className="grid h-6 w-6 place-items-center rounded-[8px]" style={{ background: "rgba(166,75,79,0.88)", color: "#FFF7EF" }} aria-label="Stop and save call recording" onClick={onStop}>
            <Square className="h-3 w-3" strokeWidth={2} fill="currentColor" />
          </button>
          <span className="ml-auto min-w-[34px] text-right text-[11px] tabular-nums" style={{ color: "rgba(255,250,243,0.78)" }}>{formatDuration(seconds)}</span>
        </>
      )}
    </div>
  )
}

function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0")
  const remainingSeconds = (seconds % 60).toString().padStart(2, "0")
  return `${minutes}:${remainingSeconds}`
}

function timestampedRecordingName(date = new Date()) {
  const pad = (value: number) => value.toString().padStart(2, "0")
  return `stroll-call-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}.mp3`
}

function saveCallRecordingMetadata(fileName: string, durationSeconds: number) {
  try {
    const raw = window.localStorage.getItem(CALL_RECORDINGS_STORAGE_KEY)
    const recordings = raw ? JSON.parse(raw) as Array<{ fileName: string; durationSeconds: number; savedAt: number }> : []
    window.localStorage.setItem(CALL_RECORDINGS_STORAGE_KEY, JSON.stringify([...recordings, { fileName, durationSeconds, savedAt: Date.now() }].slice(-50)))
  } catch {
    return
  }
}

function WeatherCurtain({ weather }: { weather: WeatherSnapshot }) {
  if (weather.kind === "clear") return null
  const count = weather.kind === "snow" ? 34 : 42
  return (
    <div className="pointer-events-none absolute inset-0 z-40 overflow-hidden" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <span
          key={index}
          className={weather.kind === "snow" ? "stroll-snowflake" : "stroll-rain-drop"}
          style={{
            left: `${(index * 37) % 100}%`,
            animationDelay: `${(index % 12) * -180}ms`,
            animationDuration: `${weather.kind === "snow" ? 3600 + (index % 8) * 240 : 980 + (index % 6) * 80}ms`,
            opacity: Math.min(0.84, (weather.intensity ?? 0.32) + 0.18),
          }}
        />
      ))}
    </div>
  )
}

function ConversationLayer({ lines, bottom, open, onHide, onShow }: { lines: typeof chatter; bottom: number; open: boolean; onHide: () => void; onShow: () => void }) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const pointerStartRef = useRef<{ x: number; y: number } | null>(null)
  const pointerMovedRef = useRef(false)

  useEffect(() => {
    if (!open) return
    const scroller = scrollerRef.current
    if (!scroller) return
    scroller.scrollTo({ top: scroller.scrollHeight })
  }, [lines.length, open])

  if (!open) {
    return (
      <button
        type="button"
        className="absolute grid h-7 w-10 place-items-center rounded-[11px] border-0 p-0"
        style={{ bottom: bottom + 3, left: LAYOUT.chatX, zIndex: 25, background: GLASS, color: "rgba(255,250,243,0.86)", border: `1px solid ${GLASS_BORDER}`, backdropFilter: "blur(10px)" }}
        aria-label="Show stroll conversation"
        onClick={onShow}
      >
        <ChevronUp className="h-4 w-4" strokeWidth={1.9} />
      </button>
    )
  }

  if (lines.length === 0) return null

  return (
    <div
      ref={scrollerRef}
      role="button"
      tabIndex={0}
      aria-label="Hide stroll conversation"
      className="absolute flex flex-col gap-1 overflow-y-auto p-0 text-left"
      style={{ bottom, left: LAYOUT.chatX, zIndex: 25, width: LAYOUT.chatWidth, maxHeight: "min(42dvh, 260px)", overscrollBehavior: "contain" }}
      onPointerDown={(event) => {
        pointerStartRef.current = { x: event.clientX, y: event.clientY }
        pointerMovedRef.current = false
      }}
      onPointerMove={(event) => {
        const start = pointerStartRef.current
        if (!start) return
        if (Math.abs(event.clientX - start.x) + Math.abs(event.clientY - start.y) > 8) pointerMovedRef.current = true
      }}
      onPointerUp={() => {
        if (!pointerMovedRef.current) onHide()
        pointerStartRef.current = null
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onHide()
      }}
    >
      {lines.map((line) => (
        <div key={`${line.name}-${line.text}`} className="w-fit shrink-0 px-2 py-1 text-[12px] leading-[1.3]" style={{ maxWidth: LAYOUT.chatWidth, borderRadius: LAYOUT.bubbleRadius, background: GLASS, border: `1px solid ${GLASS_BORDER}`, color: "rgba(255,250,243,0.9)", fontFamily: "var(--font-sans)", backdropFilter: "blur(10px)", boxShadow: "0 8px 24px -18px rgba(1,3,1,0.75)" }}>
          <span className="font-medium" style={{ color: "#F4D6CC" }}>{line.name}</span>
          <span> {line.text}</span>
        </div>
      ))}
    </div>
  )
}

function ModeSwitch({ mode, width, height, onChange }: { mode: "live" | "photo"; width: number; height: number; onChange: () => void }) {
  return (
    <button type="button" onClick={onChange} className="grid grid-cols-2 overflow-hidden rounded-[10px] p-0.5 text-[11px] font-medium" style={{ width, height, background: "rgba(31,39,62,0.92)", border: "1px solid rgba(255,250,243,0.34)", fontFamily: "var(--font-sans)", backdropFilter: "blur(10px)", boxShadow: "0 10px 24px -18px rgba(1,3,1,0.8)" }} aria-label="Toggle live photo mode">
      <span className="flex items-center justify-center gap-1 rounded-[8px] transition-colors duration-200" style={{ background: mode === "live" ? "rgba(244,214,204,0.96)" : "transparent", color: mode === "live" ? INK : "#FFF7EF" }}><Radio className="h-3 w-3" strokeWidth={1.8} />Live</span>
      <span className="flex items-center justify-center gap-1 rounded-[8px] transition-colors duration-200" style={{ background: mode === "photo" ? "rgba(244,214,204,0.96)" : "transparent", color: mode === "photo" ? INK : "#FFF7EF" }}><Camera className="h-3 w-3" strokeWidth={1.8} />Photo</span>
    </button>
  )
}

function WaveBars({ count, active }: { count: number; active: boolean }) {
  return (
    <div className="flex h-6 min-w-0 flex-1 items-center gap-[3px] overflow-hidden" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => {
        const height = 8 + ((i * 7) % 14)
        return (
          <span
            key={i}
            className={active ? "stroll-wave-bar rounded-full" : "rounded-full"}
            style={{
              width: 1.5,
              height,
              background: i % 4 === 0 ? PEACH : "rgba(255,250,243,0.72)",
              animationDelay: `${i * 70}ms`,
            }}
          />
        )
      })}
    </div>
  )
}

function SurfaceExpandButton({ expanded, label, onClick }: { expanded: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="absolute grid h-9 w-9 place-items-center rounded-[12px] border-0 p-0"
      style={{ right: LAYOUT.expandButtonInset, top: LAYOUT.expandButtonInset, zIndex: 12, background: "rgba(250,244,229,0.74)", color: INK, backdropFilter: "blur(10px)", boxShadow: "0 10px 22px -16px rgba(1,3,1,0.72)" }}
      aria-label={label}
    >
      {expanded ? <Minimize2 className="h-4 w-4" strokeWidth={1.8} /> : <Maximize2 className="h-4 w-4" strokeWidth={1.8} />}
    </button>
  )
}

function MapPanel({ weather, expanded, onToggleExpand }: { weather: WeatherSnapshot; expanded: boolean; onToggleExpand: () => void }) {
  return (
    <div className="absolute inset-0 overflow-hidden" style={{ background: "#A3B9C9" }}>
      {MAPBOX_TOKEN ? (
        <StrollMapView token={MAPBOX_TOKEN} track={sampleTrack} labels={mapLabels} annotations={mapAnnotations} weather={weather} coordinateCorrection="gcj02" />
      ) : (
        <StrollMapFallback />
      )}
      <div className="pointer-events-none absolute inset-0" style={{ background: "linear-gradient(180deg, rgba(250,244,229,0.14), rgba(244,214,204,0.06) 48%, rgba(163,185,201,0.04))", mixBlendMode: "soft-light" }} />
      <SurfaceExpandButton expanded={expanded} label={expanded ? "Shrink map" : "Expand map"} onClick={onToggleExpand} />
    </div>
  )
}

function StrollMapFallback() {
  return (
    <div className="absolute inset-0 overflow-hidden" style={{ background: "linear-gradient(135deg, #EDE5D8 0%, #B8D0CF 42%, #A3B9C9 100%)" }}>
      <div className="absolute inset-0 opacity-45" style={{
        backgroundImage:
          "linear-gradient(90deg, rgba(30,40,67,0.12) 1px, transparent 1px), linear-gradient(0deg, rgba(30,40,67,0.12) 1px, transparent 1px)",
        backgroundSize: "42px 42px",
      }} />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 412 430" preserveAspectRatio="none" aria-hidden="true">
        <path d="M-28 328 C 80 272, 118 174, 216 188 C 288 198, 312 96, 442 54" fill="none" stroke="rgba(250,244,229,0.92)" strokeWidth="25" strokeLinecap="round" />
        <path d="M-28 328 C 80 272, 118 174, 216 188 C 288 198, 312 96, 442 54" fill="none" stroke="rgba(30,40,67,0.78)" strokeWidth="13" strokeLinecap="round" />
        <path d="M-28 328 C 80 272, 118 174, 216 188 C 288 198, 312 96, 442 54" fill="none" stroke="url(#strollRouteGradient)" strokeWidth="8" strokeLinecap="round" />
        <path d="M54 430 C 94 332, 164 276, 254 248 C 326 226, 378 178, 436 112" fill="none" stroke="rgba(250,244,229,0.58)" strokeWidth="18" strokeLinecap="round" />
        <path d="M54 430 C 94 332, 164 276, 254 248 C 326 226, 378 178, 436 112" fill="none" stroke="rgba(134,87,123,0.36)" strokeWidth="3" strokeLinecap="round" />
        <defs>
          <linearGradient id="strollRouteGradient" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#6E45D9" />
            <stop offset="0.34" stopColor="#EA5AA9" />
            <stop offset="0.68" stopColor="#F3C84B" />
            <stop offset="1" stopColor="#E23B3B" />
          </linearGradient>
        </defs>
      </svg>
      <span className="absolute left-[22%] top-[63%] rounded-full px-2 py-0.5 text-[10px]" style={{ background: "rgba(250,244,229,0.68)", color: INK, fontFamily: "var(--font-sans)" }}>Start</span>
      <span className="absolute left-[49%] top-[43%] rounded-full px-2 py-0.5 text-[10px]" style={{ background: "rgba(250,244,229,0.68)", color: INK, fontFamily: "var(--font-sans)" }}>Limen</span>
      <span className="absolute left-[65%] top-[28%] rounded-full px-2 py-0.5 text-[10px]" style={{ background: "rgba(250,244,229,0.68)", color: INK, fontFamily: "var(--font-sans)" }}>Now</span>
      <div className="stroll-marker absolute left-[53%] top-[42%]" />
    </div>
  )
}