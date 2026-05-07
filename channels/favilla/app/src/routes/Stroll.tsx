import { Camera, ChevronUp, Maximize2, Minimize2, Monitor, Pause, Phone, Radio, RefreshCw, Send, Square, Video } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { ConfirmModal } from "../components/ConfirmModal"
import { StrollMapView } from "@stroll-map/StrollMapView"
import { buildPhotoAnnotations, createAiEmojiAnnotation, createSpatialRecordAnnotation } from "@stroll-map/annotations"
import { loadStoredLiveTrack, saveStoredLiveTrack, positionToTrackPoint, appendLivePoint } from "@stroll-map/liveLocation"
import { summarizeTrack } from "@stroll-map/route"
import { sampleTrack } from "@stroll-map/sampleTrack"
import { strollCellId } from "@stroll-map/spatial"
import type { StrollMapAnnotation, StrollMapLabel, StrollPhotoMarkerInput, StrollSpatialContext, StrollSpatialRecord, StrollTrackPoint, WeatherSnapshot } from "@stroll-map/types"
import { fetchWeatherSnapshot } from "@stroll-map/weather"
import { appConfig } from "../config"
import { fetchStrollHistory, fetchStrollNearby, reportStrollActionResult, sendStrollMessage, uploadFiles, writeStrollRecord, type ChatAttachment, type StoredChatMessage, type StrollClientAction } from "../lib/api"
import { captureLimenPhoto, displayLimenScreenText, fetchLimenHealth, limenStreamUrl, normalizeLimenBaseUrl, sendLimenScreenText, type LimenHealthResponse, type LimenScreenContent } from "../lib/limen"

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
  limenX: 27,
  limenY: -10,
  limenSize: 74,
  limenInner: 56,
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

type ConversationLine = {
  id: string
  name: string
  text: string
  role: "user" | "ai"
  actionType?: string
  error?: boolean
}

type LimenConnectionState = "unknown" | "online" | "streaming" | "capturing" | "error"

type Props = {
  onBack: () => void
  active: boolean
}

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
  createAiEmojiAnnotation({ id: "ai-slick", lng: sampleTrack[12].lng, lat: sampleTrack[12].lat, emoji: "⚠️", text: "slick paving", createdAt: Date.now() }),
]

export function Stroll({ onBack, active }: Props) {
  const [confirmEnd, setConfirmEnd] = useState(false)
  const [callActive, setCallActive] = useState(false)
  const [callRecordingState, setCallRecordingState] = useState<CallRecordingState>("idle")
  const [callRecordingSeconds, setCallRecordingSeconds] = useState(0)
  const [savedCallRecording, setSavedCallRecording] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const [mediaMode, setMediaMode] = useState<"live" | "photo">("live")
  const [limenBaseUrl, setLimenBaseUrl] = useState(() => appConfig.limenBaseUrl || "")
  const [limenHealth, setLimenHealth] = useState<LimenHealthResponse | null>(null)
  const [limenConnection, setLimenConnection] = useState<LimenConnectionState>(limenBaseUrl ? "online" : "unknown")
  const [limenError, setLimenError] = useState("")
  const [streamKey, setStreamKey] = useState(0)
  const [lastPhotoUrl, setLastPhotoUrl] = useState("")
  const [screenContent, setScreenContent] = useState<LimenScreenContent>({ type: "status", text: "ready" })
  const [conversationOpen, setConversationOpen] = useState(true)
  const [conversationLines, setConversationLines] = useState<ConversationLine[]>([])
  const [draft, setDraft] = useState("")
  const [sending, setSending] = useState(false)
  const [liveTrack, setLiveTrack] = useState<StrollTrackPoint[]>(() => loadStoredLiveTrack())
  const [nearbyRecords, setNearbyRecords] = useState<StrollSpatialRecord[]>([])
  const [nearbyVersion, setNearbyVersion] = useState("")
  const [placeKind, setPlaceKind] = useState<StrollSpatialContext["placeKind"]>("unknown")
  const [screenExpanded, setScreenExpanded] = useState(false)
  const [mapExpanded, setMapExpanded] = useState(false)
  const [weather, setWeather] = useState<WeatherSnapshot>({ kind: "clear", intensity: 0.24, source: "fallback" })
  const [selectedAnnotation, setSelectedAnnotation] = useState<StrollMapAnnotation | null>(null)
  const foldStartYRef = useRef<number | null>(null)
  const wasActiveRef = useRef(active)
  const lastPhotoUrlRef = useRef("")
  const activeTrack = liveTrack.length ? liveTrack : sampleTrack
  const currentPoint = activeTrack[activeTrack.length - 1]
  const normalizedLimenBaseUrl = useMemo(() => normalizeLimenBaseUrl(limenBaseUrl), [limenBaseUrl])
  const streamUrl = useMemo(() => normalizedLimenBaseUrl ? limenStreamUrl(normalizedLimenBaseUrl) : "", [normalizedLimenBaseUrl])

  const dynamicAnnotations = useMemo(() => {
    const photos: StrollPhotoMarkerInput[] = nearbyRecords
      .filter((record) => record.kind === "photo" && record.attachment)
      .map((record) => ({
        id: record.attachment?.id || record.id,
        lng: record.lng,
        lat: record.lat,
        url: record.attachment?.url,
        thumbUrl: record.attachment?.thumbUrl,
        takenAt: record.attachment?.takenAt || record.createdAt,
        source: record.attachment?.source === "limen" || record.attachment?.source === "replay" ? record.attachment.source : "phone",
      }))
    const photoIds = new Set(nearbyRecords.filter((record) => record.kind === "photo" && record.attachment).map((record) => record.id))
    const richPhotoAnnotations = buildPhotoAnnotations(photos).map((annotation) => ({
      ...annotation,
      text: annotation.count && annotation.count > 1 ? `${annotation.count} photos` : "Limen photo",
    }))
    const richRecords = nearbyRecords
      .filter((record) => !photoIds.has(record.id))
      .filter((record) => record.text || record.emoji || record.attachment)
      .map(createSpatialRecordAnnotation)
    return [...mapAnnotations, ...richPhotoAnnotations, ...richRecords]
  }, [nearbyRecords])

  const dynamicLabels = useMemo(() => [
    ...mapLabels,
    ...nearbyRecords.slice(0, 4).filter((record) => record.text).map((record) => ({
      id: `nearby-${record.id}`,
      lng: record.lng,
      lat: record.lat,
      text: String(record.text).slice(0, 28),
      tone: record.origin === "ai" ? "note" as const : "current" as const,
    })),
  ], [nearbyRecords])

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
    const onConfigChange = () => setLimenBaseUrl(appConfig.limenBaseUrl || "")
    window.addEventListener("favilla:config-changed", onConfigChange)
    return () => window.removeEventListener("favilla:config-changed", onConfigChange)
  }, [])

  useEffect(() => () => {
    if (lastPhotoUrlRef.current) URL.revokeObjectURL(lastPhotoUrlRef.current)
  }, [])

  useEffect(() => {
    if (!active || !normalizedLimenBaseUrl) return
    void refreshLimenHealth()
  }, [active, normalizedLimenBaseUrl])

  useEffect(() => {
    if (!active) return
    let cancelled = false
    setLiveTrack((track) => track.length ? track : loadStoredLiveTrack())
    fetchStrollHistory().then((history) => {
      if (cancelled || !history.ok) return
      setConversationLines(historyToLines(history.messages || []))
    })
    return () => { cancelled = true }
  }, [active])

  useEffect(() => {
    if (!active || !("geolocation" in navigator)) return
    const watchId = navigator.geolocation.watchPosition(
      (position) => {
        const point = positionToTrackPoint(position)
        setLiveTrack((track) => {
          const next = appendLivePoint(track.length ? track : loadStoredLiveTrack(), point)
          saveStoredLiveTrack(next)
          return next
        })
      },
      () => undefined,
      { enableHighAccuracy: true, maximumAge: 10_000, timeout: 12_000 },
    )
    return () => navigator.geolocation.clearWatch(watchId)
  }, [active])

  useEffect(() => {
    if (!active || !currentPoint) return
    let cancelled = false
    fetchStrollNearby(currentPoint, 50).then((nearby) => {
      if (cancelled || !nearby.ok) return
      setNearbyRecords(nearby.records || [])
      setNearbyVersion(nearby.contextVersion || "")
    })
    return () => { cancelled = true }
  }, [active, currentPoint?.id, currentPoint?.lng, currentPoint?.lat])

  useEffect(() => {
    if (!selectedAnnotation) return
    if (!dynamicAnnotations.some((annotation) => annotation.id === selectedAnnotation.id)) setSelectedAnnotation(null)
  }, [dynamicAnnotations, selectedAnnotation])

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
    setDraft("")
    setSending(false)
    setConversationOpen(true)
    setScreenExpanded(false)
    setMapExpanded(false)
    setCallActive(false)
    setCallRecordingState("idle")
    setCallRecordingSeconds(0)
    setSavedCallRecording(null)
    setRecording(false)
  }

  async function refreshLimenHealth() {
    if (!normalizedLimenBaseUrl) {
      setLimenConnection("unknown")
      setLimenHealth(null)
      return null
    }
    try {
      const health = await fetchLimenHealth(normalizedLimenBaseUrl)
      setLimenHealth(health)
      setLimenConnection(mediaMode === "live" ? "streaming" : "online")
      setLimenError("")
      return health
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setLimenConnection("error")
      setLimenError(message)
      return null
    }
  }

  function changeMediaMode(nextMode: "live" | "photo") {
    setMediaMode(nextMode)
    if (nextMode === "live") {
      setStreamKey((key) => key + 1)
      setLimenConnection(normalizedLimenBaseUrl ? "streaming" : "unknown")
    }
  }

  function buildContext(): StrollSpatialContext {
    const summary = summarizeTrack(activeTrack)
    return {
      current: currentPoint ? { ...currentPoint, cellId: strollCellId(currentPoint), placeKind } : undefined,
      route: {
        points: activeTrack.slice(-80),
        distanceKm: summary.distanceKm,
        averageSpeedKmh: summary.averageSpeedKmh,
      },
      annotations: dynamicAnnotations,
      nearbyMemories: nearbyRecords.map((record) => ({
        id: record.id,
        lng: record.lng,
        lat: record.lat,
        radiusM: record.radiusM,
        cellId: record.cellId,
        distanceM: record.distanceM,
        bearingDeg: record.bearingDeg,
        placeKind: record.placeKind,
        title: record.text || record.kind,
        lastSeenAt: record.updatedAt,
        sourceIds: [record.id],
        origin: record.origin,
      })),
      spatialRecords: nearbyRecords,
      cellId: currentPoint ? strollCellId(currentPoint) : undefined,
      placeKind,
      contextVersion: nearbyVersion,
      weather,
      lightPreset: localLightPreset(),
    }
  }

  async function sendDraft() {
    const text = draft.trim()
    if (!text || sending) return
    const id = `local-${Date.now()}`
    setDraft("")
    setSending(true)
    setConversationOpen(true)
    setConversationLines((lines) => [...lines, { id, name: appConfig.userName || "you", text, role: "user" }])
    if (currentPoint) {
      void writeStrollRecord({ kind: "note", origin: "user", lng: currentPoint.lng, lat: currentPoint.lat, text, placeKind: "unknown" }).catch(() => undefined)
    }
    try {
      const result = await sendStrollMessage(text, buildContext())
      if (!result.ok) {
        setConversationLines((lines) => [...lines, { id: `err-${Date.now()}`, name: appConfig.aiName || "Favilla", text: result.error || "Stroll failed", role: "ai", error: true }])
        return
      }
      const reply = result.reply || ""
      typeAiReply(reply)
      if (result.stroll_records?.length) {
        setNearbyRecords((records) => mergeRecords(records, result.stroll_records || []))
      }
      if (result.stroll_actions?.length) {
        await handleStrollActions(result.stroll_actions)
      }
      if (currentPoint && reply) {
        void writeStrollRecord({ kind: "note", origin: "ai", lng: currentPoint.lng, lat: currentPoint.lat, text: reply.slice(0, 500), placeKind: "unknown" }).catch(() => undefined)
      }
    } finally {
      setSending(false)
    }
  }

  function typeAiReply(fullText: string) {
    const id = `ai-${Date.now()}`
    const chars = Array.from(fullText)
    let count = 0
    setConversationLines((lines) => [...lines, { id, name: appConfig.aiName || "Favilla", text: "", role: "ai" }])
    const timer = window.setInterval(() => {
      count = Math.min(chars.length, count + 3)
      const nextText = chars.slice(0, count).join("")
      setConversationLines((lines) => lines.map((line) => line.id === id ? { ...line, text: nextText } : line))
      if (count >= chars.length) window.clearInterval(timer)
    }, 34)
  }

  async function handleStrollActions(actions: StrollClientAction[]) {
    for (const action of actions) {
      if (action.type === "view_camera") {
        await executeViewCameraAction(action)
        continue
      }
      if (action.type === "capture_photo") {
        await executeCapturePhotoAction(action)
        continue
      }
      if (action.type === "set_limen_screen") {
        await executeSetLimenScreenAction(action)
        continue
      }
      if (action.type === "refresh_nearby" && currentPoint) {
        const nearby = await fetchStrollNearby(currentPoint, 50).catch((error) => ({ ok: false, records: [], contextVersion: "", error: error instanceof Error ? error.message : "refresh failed" }))
        if (nearby.ok) {
          setNearbyRecords(nearby.records || [])
          setNearbyVersion(nearby.contextVersion || "")
        }
        appendActionLine(action, nearby.ok ? "nearby refreshed" : nearby.error || "refresh failed", !nearby.ok)
        void reportStrollActionResult({ actionId: action.id, type: action.type, status: nearby.ok ? "ok" : "error", error: nearby.error }).catch(() => undefined)
        continue
      }
      appendActionLine(action, "action queued")
      void reportStrollActionResult({ actionId: action.id, type: action.type, status: "queued", note: "received by Stroll web preview; hardware execution waits for device" }).catch(() => undefined)
    }
  }

  async function executeViewCameraAction(action: StrollClientAction) {
    changeMediaMode("live")
    const health = await refreshLimenHealth()
    const ok = Boolean(health?.ok && streamUrl)
    appendActionLine(action, ok ? "camera live opened" : limenError || "Limen camera unavailable", !ok)
    void reportStrollActionResult({ actionId: action.id, type: action.type, status: ok ? "ok" : "error", error: ok ? undefined : limenError || "missing Limen URL" }).catch(() => undefined)
  }

  async function executeSetLimenScreenAction(action: StrollClientAction) {
    const content = actionScreenContent(action)
    setScreenContent(content)
    if (!normalizedLimenBaseUrl) {
      appendActionLine(action, "Limen URL missing", true)
      void reportStrollActionResult({ actionId: action.id, type: action.type, status: "error", error: "missing Limen URL" }).catch(() => undefined)
      return
    }
    try {
      await sendLimenScreenText(normalizedLimenBaseUrl, content)
      appendActionLine(action, "Limen screen updated")
      void reportStrollActionResult({ actionId: action.id, type: action.type, status: "ok", text: displayLimenScreenText(content) }).catch(() => undefined)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setLimenError(message)
      setLimenConnection("error")
      appendActionLine(action, message, true)
      void reportStrollActionResult({ actionId: action.id, type: action.type, status: "error", error: message }).catch(() => undefined)
    }
  }

  async function executeCapturePhotoAction(action: StrollClientAction) {
    if (!normalizedLimenBaseUrl) {
      appendActionLine(action, "Limen URL missing", true)
      void reportStrollActionResult({ actionId: action.id, type: action.type, status: "error", error: "missing Limen URL" }).catch(() => undefined)
      return
    }
    changeMediaMode("photo")
    setLimenConnection("capturing")
    try {
      const file = await captureLimenPhoto(normalizedLimenBaseUrl)
      if (lastPhotoUrlRef.current) URL.revokeObjectURL(lastPhotoUrlRef.current)
      const nextPhotoUrl = URL.createObjectURL(file)
      lastPhotoUrlRef.current = nextPhotoUrl
      setLastPhotoUrl(nextPhotoUrl)
      setLimenConnection("online")

      const upload = await uploadFiles([file])
      if (!upload.ok || !upload.files?.length) throw new Error(upload.error || "photo upload failed")
      const attachment = upload.files[0]
      await recordLimenPhotoMarker(action, attachment)
      appendActionLine(action, "photo captured and sent")
      void reportStrollActionResult({ actionId: action.id, type: action.type, status: "ok", attachment }).catch(() => undefined)
      await sendLimenPhotoToAi(action, upload.files)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setLimenError(message)
      setLimenConnection("error")
      appendActionLine(action, message, true)
      void reportStrollActionResult({ actionId: action.id, type: action.type, status: "error", error: message }).catch(() => undefined)
    }
  }

  async function recordLimenPhotoMarker(action: StrollClientAction, attachment: ChatAttachment) {
    if (!currentPoint) return
    const payload = action.payload || {}
    const description = String(payload.text || payload.reason || "Limen photo").slice(0, 90)
    const emoji = String(payload.emoji || "camera").slice(0, 20)
    const result = await writeStrollRecord({
      kind: "photo",
      origin: "limen",
      lng: currentPoint.lng,
      lat: currentPoint.lat,
      text: description,
      emoji,
      placeKind,
      attachment: {
        id: attachment.path || attachment.name,
        url: attachment.path,
        takenAt: Date.now(),
        source: "limen",
      },
    }).catch(() => null)
    const record = result?.ok ? result.record : undefined
    if (record) setNearbyRecords((records) => mergeRecords(records, [record]))
  }

  async function sendLimenPhotoToAi(action: StrollClientAction, attachments: ChatAttachment[]) {
    const payload = action.payload || {}
    const reason = String(payload.reason || "look at the captured wearable camera photo").slice(0, 160)
    const result = await sendStrollMessage(`[limen camera] ${reason}`, buildContext(), attachments)
    if (!result.ok) {
      appendActionLine(action, result.error || "photo sent, AI review failed", true)
      return
    }
    if (result.reply) typeAiReply(result.reply)
    if (result.stroll_records?.length) setNearbyRecords((records) => mergeRecords(records, result.stroll_records || []))
  }

  function actionScreenContent(action: StrollClientAction): LimenScreenContent {
    const payload = action.payload || {}
    const text = String(payload.text || payload.message || payload.reason || payload.emoji || "ready")
    const emoji = String(payload.emoji || "")
    return { type: text ? "message" : "emoji", text, emoji }
  }

  function appendActionLine(action: StrollClientAction, text: string, error = false) {
    setConversationLines((lines) => [...lines, { id: `action-${action.id}`, name: "tool", text, role: "ai", actionType: action.type, error }])
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
          {mediaMode === "live" && streamUrl ? (
            <img
              key={streamKey}
              src={streamUrl}
              alt="Limen live preview"
              className="absolute inset-0 h-full w-full object-cover"
              onLoad={() => setLimenConnection("streaming")}
              onError={() => { setLimenConnection("error"); setLimenError("Limen stream failed") }}
            />
          ) : mediaMode === "photo" && lastPhotoUrl ? (
            <img src={lastPhotoUrl} alt="Last Limen capture" className="absolute inset-0 h-full w-full object-cover" />
          ) : null}
          <div className="absolute left-4 top-4 flex items-center gap-2 rounded-full px-3 py-1.5" style={{ background: "rgba(1,3,1,0.36)", color: "#fff" }}>
            {mediaMode === "live" ? <Video className="h-3.5 w-3.5" strokeWidth={1.8} /> : <Camera className="h-3.5 w-3.5" strokeWidth={1.8} />}
            <span className="text-[11px] font-medium uppercase tracking-[0.18em]">{mediaMode}</span>
            <span className="max-w-[118px] truncate text-[10px] opacity-75">{limenConnection === "error" ? limenError : limenHealth?.ip || (normalizedLimenBaseUrl ? "ready" : "set url")}</span>
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
            left: LAYOUT.limenX,
            top: LAYOUT.limenY,
            width: LAYOUT.limenSize,
            height: LAYOUT.limenSize,
            background: `radial-gradient(circle at 38% 32%, #CBE8E9, ${WATER} 42%, ${BLUE} 100%)`,
            border: "3px solid rgba(255,255,255,0.72)",
            boxShadow: "0 14px 34px -14px rgba(1,3,1,0.72)",
          }}
          aria-label="Limen screen preview"
        >
          <div className="grid place-items-center rounded-full px-2 text-center text-[10px] leading-tight" style={{ width: LAYOUT.limenInner, height: LAYOUT.limenInner, background: "rgba(30,40,67,0.82)", color: "rgba(255,250,243,0.92)", boxShadow: `0 -9px 0 ${PEACH} inset`, fontFamily: "var(--font-sans)" }}>{displayLimenScreenText(screenContent)}</div>
        </div>
        <div className="absolute" style={{ right: LAYOUT.switchRight, top: LAYOUT.switchTop }}>
          <ModeSwitch mode={mediaMode} width={LAYOUT.switchWidth} height={LAYOUT.switchHeight} onChange={() => changeMediaMode(mediaMode === "live" ? "photo" : "live")} />
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
          track={activeTrack}
          labels={dynamicLabels}
          annotations={dynamicAnnotations}
          onPlaceKindChange={setPlaceKind}
          onAnnotationClick={setSelectedAnnotation}
          onToggleExpand={() => {
            setMapExpanded((expanded) => !expanded)
            setScreenExpanded(false)
          }}
        />
        <AnnotationDetail annotation={selectedAnnotation} onClose={() => setSelectedAnnotation(null)} />
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
                    <input
                      className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-[rgba(255,250,243,0.62)] disabled:opacity-60"
                      style={{ fontFamily: "var(--font-sans)" }}
                      placeholder="Say…"
                      value={draft}
                      disabled={sending}
                      onChange={(event) => setDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault()
                          void sendDraft()
                        }
                      }}
                    />
                    <button type="button" className="grid h-6 w-6 place-items-center rounded-full disabled:opacity-45" style={{ background: "rgba(244,214,204,0.92)", color: INK }} aria-label="Send stroll message" disabled={sending || !draft.trim()} onClick={() => { void sendDraft() }}>
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

function ConversationLayer({ lines, bottom, open, onHide, onShow }: { lines: ConversationLine[]; bottom: number; open: boolean; onHide: () => void; onShow: () => void }) {
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
        <div key={line.id} className="w-fit shrink-0 px-2 py-1 text-[12px] leading-[1.3]" style={{ maxWidth: LAYOUT.chatWidth, borderRadius: LAYOUT.bubbleRadius, background: line.error ? "rgba(166,75,79,0.72)" : GLASS, border: `1px solid ${GLASS_BORDER}`, color: "rgba(255,250,243,0.9)", fontFamily: "var(--font-sans)", backdropFilter: "blur(10px)", boxShadow: "0 8px 24px -18px rgba(1,3,1,0.75)" }}>
          <span className="inline-flex items-center gap-1 font-medium" style={{ color: line.role === "user" ? "#CBE8E9" : "#F4D6CC" }}>
            {line.actionType ? <StrollActionIcon type={line.actionType} /> : null}{line.name}
          </span>
          <span> {line.text}</span>
        </div>
      ))}
    </div>
  )
}

function StrollActionIcon({ type }: { type: string }) {
  if (type === "view_camera") return <Video className="h-3 w-3" strokeWidth={1.8} />
  if (type === "capture_photo") return <Camera className="h-3 w-3" strokeWidth={1.8} />
  if (type === "set_limen_screen") return <Monitor className="h-3 w-3" strokeWidth={1.8} />
  if (type === "refresh_nearby") return <RefreshCw className="h-3 w-3" strokeWidth={1.8} />
  return <Radio className="h-3 w-3" strokeWidth={1.8} />
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

function AnnotationDetail({ annotation, onClose }: { annotation: StrollMapAnnotation | null; onClose: () => void }) {
  if (!annotation) return null
  const date = formatAnnotationDate(annotation.updatedAt || annotation.createdAt || annotation.photos?.[0]?.takenAt)
  const source = annotation.origin || annotation.source
  const attachment = annotation.attachment || annotation.photos?.[0]
  return (
    <div className="absolute left-4 right-4 top-4 z-20 rounded-[14px] px-3 py-2.5" style={{ background: "rgba(38,48,74,0.78)", color: "rgba(255,250,243,0.94)", border: `1px solid ${GLASS_BORDER}`, backdropFilter: "blur(14px)", boxShadow: "0 18px 34px -22px rgba(1,3,1,0.8)", fontFamily: "var(--font-sans)" }}>
      <div className="flex items-start gap-2">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-[12px] text-[19px]" style={{ background: "rgba(255,250,243,0.14)", color: "#FFF7EF" }}>
          {annotation.emoji || (annotation.kind === "photo" ? "📷" : "✦")}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 text-[13px] font-medium leading-snug">{annotation.text || (annotation.kind === "photo" ? "Photo marker" : "AI marker")}</div>
            <button type="button" onClick={onClose} className="grid h-6 w-6 shrink-0 place-items-center rounded-[8px] border-0 p-0" style={{ background: "rgba(255,250,243,0.12)", color: "rgba(255,250,243,0.82)" }} aria-label="Close marker detail">×</button>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] uppercase tracking-[0.12em]" style={{ color: "rgba(255,250,243,0.62)" }}>
            {date ? <span>{date}</span> : null}
            {annotation.placeKind ? <span>{annotation.placeKind}</span> : null}
            {source ? <span>{source}</span> : null}
            {typeof annotation.distanceM === "number" ? <span>{Math.round(annotation.distanceM)}m</span> : null}
            {annotation.count && annotation.count > 1 ? <span>{annotation.count} photos</span> : null}
          </div>
          {attachment?.url ? <div className="mt-1 truncate text-[11px]" style={{ color: "rgba(255,250,243,0.72)" }}>{attachment.url}</div> : null}
        </div>
      </div>
    </div>
  )
}

function formatAnnotationDate(value?: number) {
  if (!value) return ""
  try {
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value))
  } catch {
    return ""
  }
}

function MapPanel({ weather, expanded, track, labels, annotations, onPlaceKindChange, onAnnotationClick, onToggleExpand }: { weather: WeatherSnapshot; expanded: boolean; track: StrollTrackPoint[]; labels: StrollMapLabel[]; annotations: StrollMapAnnotation[]; onPlaceKindChange: (placeKind: NonNullable<StrollSpatialContext["placeKind"]>) => void; onAnnotationClick: (annotation: StrollMapAnnotation) => void; onToggleExpand: () => void }) {
  return (
    <div className="absolute inset-0 overflow-hidden" style={{ background: "#A3B9C9" }}>
      {MAPBOX_TOKEN ? (
        <StrollMapView token={MAPBOX_TOKEN} track={track} labels={labels} annotations={annotations} weather={weather} coordinateCorrection="gcj02" onPlaceKindChange={onPlaceKindChange} onAnnotationClick={onAnnotationClick} />
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

function historyToLines(messages: StoredChatMessage[]): ConversationLine[] {
  return messages
    .filter((message) => message.text && (message.role === "user" || message.role === "ai"))
    .map((message) => ({
      id: message.id,
      name: message.role === "user" ? appConfig.userName || "you" : appConfig.aiName || "Favilla",
      text: String(message.text),
      role: message.role,
      error: Boolean(message.error),
    }))
}

function localLightPreset(): StrollSpatialContext["lightPreset"] {
  const hour = new Date().getHours()
  if (hour < 6) return "night"
  if (hour < 9) return "dawn"
  if (hour < 17) return "day"
  if (hour < 20) return "dusk"
  return "night"
}

function mergeRecords(existing: StrollSpatialRecord[], incoming: StrollSpatialRecord[]) {
  const byId = new Map(existing.map((record) => [record.id, record]))
  incoming.forEach((record) => byId.set(record.id, record))
  return Array.from(byId.values()).sort((a, b) => (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt))
}