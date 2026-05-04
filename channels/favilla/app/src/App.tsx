import { useEffect, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import {
  ChevronLeft,
  ChevronRight,
  Scissors,
  Plus,
  Camera,
  Mic,
  Send,
  Play,
  FileText,
  Image as ImageIcon,
  Brain,
  Search,
  CheckCircle2,
  X,
  Copy,
  Check,
} from "lucide-react"
import { LockIcon } from "./components/LockIcon"
import { RecallIcon } from "./components/RecallIcon"
import { HourglassIcon } from "./components/HourglassIcon"
import { ConfirmModal } from "./components/ConfirmModal"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { fetchChatHistory, recordChatMessage, sendChat, uploadFiles, recallNow, cutFlow, processFlow, type ChatAttachment } from "./lib/api"
import { appConfig, saveConfig } from "./config"

// Module-level set of bubble ids whose entrance animation has already played.
// Skipping replay prevents jank on tab switch / re-render of long histories.
const SEEN_BUBBLE_IDS = new Set<string>()

type Attachment =
  | { kind: "voice"; seconds: number }
  | { kind: "file"; name: string; size?: string | number }
  | { kind: "image"; name: string }

type ThinkStep = {
  kind: "think" | "search" | "check" | "native"
  text: string
  result?: string
  source?: "marker" | "native"
}

type Msg = {
  id: string
  role: "user" | "ai"
  text?: string
  /** Minutes since epoch. Used to decide whether to show a time separator. */
  t: number
  attachments?: Attachment[]
  thinking?: ThinkStep[]
  thinkingLocked?: boolean
  /** Non-bubble divider rendered in place of a chat bubble. */
  divider?: { kind: "scissor" | "recall"; label?: string }
  /** True if recall was armed when this user message was sent. Renders 🌠. */
  recallUsed?: boolean
  /** Render as centered light-red note instead of a chat bubble. */
  error?: boolean
}

const INK = "#3f2f29"

function formatT(t: number) {
  const d = new Date(t * 60_000)
  const h = d.getHours()
  const m = d.getMinutes()
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`
}

function currentT() {
  return Math.floor(Date.now() / 60_000)
}

const SEND_MERGE_WINDOW_MS = 60_000

// ---------- Voice (waveform) chip ----------
function VoiceChip({ seconds }: { seconds: number }) {
  const bars = [4, 9, 6, 12, 8, 14, 10, 6, 11, 7, 13, 8]
  return (
    <div
      className="flex items-center gap-2.5 rounded-full px-3"
      style={{
        background: "rgba(255,255,255,0.78)",
        border: "1px solid rgba(63,47,41,0.1)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        width: 200,
        height: 44,
      }}
    >
      <button
        type="button"
        className="grid h-7 w-7 shrink-0 place-items-center rounded-full"
        style={{ background: "var(--color-cocoa)", color: "var(--color-cream)" }}
      >
        <Play className="h-3 w-3" strokeWidth={2} fill="currentColor" />
      </button>
      <div className="flex flex-1 items-center justify-between gap-[2px]">
        {bars.map((h, i) => (
          <span
            key={i}
            className="block w-[2px] rounded-full"
            style={{ height: h, background: "rgba(63,47,41,0.5)" }}
          />
        ))}
      </div>
      <span
        className="text-[11px] tabular-nums"
        style={{ color: "rgba(63,47,41,0.65)", fontFamily: "var(--font-mono)" }}
      >
        0:{String(seconds).padStart(2, "0")}
      </span>
    </div>
  )
}

// ---------- File / image pill (colored by type) ----------

function FilePill({ a }: { a: Extract<Attachment, { kind: "file" | "image" }> }) {
  const isImage = a.kind === "image"
  const Icon = isImage ? ImageIcon : FileText
  const bgColor = isImage
    ? "rgba(199,195,176,0.85)" // sage
    : "rgba(255,232,214,0.92)" // peach
  const iconColor = isImage ? "#5a5840" : "var(--color-cocoa)"
  return (
    <div
      className="inline-flex items-center gap-[3px] rounded-[6px] px-[5px] py-[2px]"
      style={{
        background: bgColor,
        width: 96,
      }}
    >
      <Icon
        className="h-[14px] w-[14px] shrink-0"
        strokeWidth={1.7}
        style={{ color: iconColor }}
      />
      <span
        className="min-w-0 truncate text-[12px]"
        style={{
          color: INK,
          fontFamily: "var(--font-sans)",
          lineHeight: "1.3",
        }}
      >
        {a.name}
      </span>
    </div>
  )
}

function Attachments({ list, isUser }: { list: Attachment[]; isUser: boolean }) {
  return (
    <div
      className={`flex flex-wrap items-center gap-1.5 ${
        isUser ? "justify-end" : "justify-start"
      }`}
    >
      {list.map((a, i) =>
        a.kind === "voice" ? (
          <VoiceChip key={i} seconds={a.seconds} />
        ) : (
          <FilePill key={i} a={a} />
        ),
      )}
    </div>
  )
}

// ---------- Thinking chain (Claude-style) ----------
function ThinkIcon({ kind }: { kind: ThinkStep["kind"] }) {
  const Icon =
    kind === "search" ? Search : kind === "check" ? CheckCircle2 : Brain
  return <Icon className="h-3.5 w-3.5" strokeWidth={1.6} />
}

function ThinkingChain({ steps, locked, peerName }: { steps: ThinkStep[]; locked?: boolean; peerName?: string }) {
  const [open, setOpen] = useState(false)
  if (locked) {
    return (
      <div className="w-full">
        <div
          className="mb-2 inline-flex items-center gap-1 text-[12px]"
          style={{ color: "rgba(63,47,41,0.45)", fontFamily: "var(--font-sans)" }}
        >
          <span>{(peerName || "Fiet")} thought silently</span>
          <LockIcon className="h-3.5 w-3" strokeWidth={1} />
        </div>
      </div>
    )
  }
  return (
    <div className="w-full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mb-2 inline-flex items-center gap-1 text-[12px]"
        style={{
          color: "rgba(63,47,41,0.55)",
          fontFamily: "var(--font-sans)",
        }}
      >
        <span>{open ? "Hide thinking" : "Show thinking"}</span>
        <ChevronRight
          className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`}
          strokeWidth={2}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            className="mb-3 overflow-hidden"
          >
            <ol className="flex flex-col gap-1.5">
              {steps.map((s, i) => {
                const isLast = i === steps.length - 1
                return (
                  <li key={i} className="grid grid-cols-[16px_1fr] gap-2.5">
                    {/* icon column with rail */}
                    <div className="relative flex flex-col items-center">
                      <div
                        className="grid h-4 w-4 place-items-center"
                        style={{ color: "rgba(63,47,41,0.6)" }}
                      >
                        <ThinkIcon kind={s.kind} />
                      </div>
                      {!isLast && (
                        <span
                          className="mt-0.5 w-px flex-1"
                          style={{ background: "rgba(63,47,41,0.18)" }}
                        />
                      )}
                    </div>
                    {/* content */}
                    <div className="pb-1">
                      <div
                        className="text-[13px] leading-[1.5]"
                        style={{
                          color: "rgba(63,47,41,0.78)",
                          fontFamily: "var(--font-sans)",
                        }}
                      >
                        {s.text}
                      </div>
                      {s.result && (
                        <div
                          className="mt-1 inline-block rounded-[6px] px-2 py-0.5 text-[11.5px]"
                          style={{
                            background: "rgba(102,78,68,0.08)",
                            color: "rgba(63,47,41,0.7)",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {s.result}
                        </div>
                      )}
                    </div>
                  </li>
                )
              })}
            </ol>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ---------- Bubble ----------
function NameTag({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="px-1 text-[14.5px]"
      style={{
        color: "rgba(63,47,41,0.7)",
        fontFamily: "var(--font-serif)",
        fontStyle: "italic",
        fontWeight: 600,
      }}
    >
      {children}
    </span>
  )
}

function ErrorNote({ text }: { text: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="flex w-full justify-center"
    >
      <span
        className="text-[12px] leading-[1.4]"
        style={{
          color: "rgba(180,60,60,0.85)",
          fontFamily: "var(--font-sans)",
          letterSpacing: "0.02em",
          maxWidth: "82%",
          textAlign: "center",
        }}
      >
        {text}
      </span>
    </motion.div>
  )
}

function Bubble({
  msg,
  peerName,
  showName,
}: {
  msg: Msg
  peerName: string
  showName: boolean
}) {
  const isUser = msg.role === "user"
  const voiceAttachments = (msg.attachments ?? []).filter(
    (a) => a.kind === "voice",
  )
  const fileAttachments = (msg.attachments ?? []).filter(
    (a) => a.kind !== "voice",
  )
  // Only animate the FIRST time we see this message id; on re-render skip
  // entrance entirely (no jank scrolling/switching tabs back to chat).
  const wasSeen = SEEN_BUBBLE_IDS.has(msg.id)
  if (!wasSeen) SEEN_BUBBLE_IDS.add(msg.id)
  return (
    <motion.div
      initial={wasSeen ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div
        className={`flex max-w-[82%] flex-col gap-1 ${
          isUser ? "items-end" : "items-start"
        }`}
      >
        {showName &&
          (isUser ||
            !!msg.text ||
            !!msg.thinkingLocked ||
            (msg.thinking?.length ?? 0) > 0 ||
            (msg.attachments?.length ?? 0) > 0) && (
            <NameTag>{isUser ? (appConfig.userName || "you") : peerName}</NameTag>
          )}

        {!isUser && (msg.thinkingLocked || (msg.thinking && msg.thinking.length > 0)) && (
          <ThinkingChain steps={msg.thinking || []} locked={msg.thinkingLocked} peerName={peerName} />
        )}

        {voiceAttachments.length > 0 && (
          <Attachments list={voiceAttachments} isUser={isUser} />
        )}

        {msg.text && (
          <BubbleBody
            text={msg.text}
            isUser={isUser}
            recallUsed={!!msg.recallUsed}
          />
        )}

        {fileAttachments.length > 0 && (
          <Attachments list={fileAttachments} isUser={isUser} />
        )}
      </div>
    </motion.div>
  )
}

function BubbleBody({
  text,
  isUser,
  recallUsed,
}: {
  text: string
  isUser: boolean
  recallUsed: boolean
}) {
  const [showCopy, setShowCopy] = useState(false)
  const [copied, setCopied] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  // Any interaction outside this bubble dismisses the copy button. The
  // listener attaches only while the menu is open and uses `pointerdown`
  // so it fires on the very first touch/click anywhere else.
  useEffect(() => {
    if (!showCopy) return
    function onDown(e: PointerEvent) {
      if (!wrapRef.current) return
      if (!wrapRef.current.contains(e.target as Node)) setShowCopy(false)
    }
    window.addEventListener("pointerdown", onDown, true)
    return () => window.removeEventListener("pointerdown", onDown, true)
  }, [showCopy])
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
      window.setTimeout(() => setShowCopy(false), 1200)
    } catch {
      // ignore
    }
  }
  return (
    <div ref={wrapRef} className="relative inline-block" style={{ overflow: "visible" }}>
      <div
        onClick={() => setShowCopy((v) => !v)}
        className={`md relative px-4 py-3 text-[14.5px] leading-[1.6] ${
          isUser
            ? "rounded-[18px] rounded-br-[6px]"
            : "rounded-[18px] rounded-bl-[6px]"
        }`}
        style={{
          background: isUser
            ? "rgba(208,188,190,0.72)"
            : "rgba(235,235,235,0.62)",
          color: INK,
          border: isUser
            ? "1px solid rgba(255,255,255,0.28)"
            : "1px solid rgba(255,255,255,0.5)",
          boxShadow:
            "0 1px 0 rgba(255,255,255,0.4) inset, 0 6px 20px -10px rgba(0,0,0,0.25)",
          cursor: "pointer",
        }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
      {recallUsed && (
        <span
          aria-label="recall used"
          className="pointer-events-none select-none"
          style={{
            position: "absolute",
            right: -4,
            bottom: -4,
            width: 14,
            height: 14,
            display: "block",
            lineHeight: 0,
            filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.22))",
            color: "#FAEC8C",
            transform: "rotate(12deg)",
            zIndex: 2,
          }}
        >
          <RecallIcon className="h-[14px] w-[14px]" strokeWidth={1.45} color="#FAEC8C" />
        </span>
      )}
      <AnimatePresence>
        {showCopy && (
          <motion.button
            type="button"
            initial={{ opacity: 0, scale: 0.92, x: isUser ? 4 : -4 }}
            animate={{ opacity: 1, scale: 1, x: 0 }}
            exit={{ opacity: 0, scale: 0.92 }}
            transition={{ duration: 0.14, ease: "easeOut" }}
            onClick={(e) => { e.stopPropagation(); copy() }}
            className="absolute top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-full"
            style={{
              [isUser ? "right" : "left"]: "calc(100% + 6px)",
              background: "transparent",
              border: "none",
              boxShadow: "none",
              filter: "drop-shadow(0 1px 2px rgba(255,250,243,0.65)) drop-shadow(0 2px 4px rgba(63,47,41,0.24))",
              color: copied ? "#7a8a52" : INK,
            }}
            aria-label={copied ? "Copied" : "Copy message"}
          >
            {copied ? (
              <Check className="h-[14px] w-[14px]" strokeWidth={2} />
            ) : (
              <Copy className="h-[14px] w-[14px]" strokeWidth={1.7} />
            )}
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  )
}

// ---------- Time separator (only when gap > 10min) ----------
function TimeSeparator({ t }: { t: number }) {
  return (
    <div className="my-1 flex items-center justify-center">
      <span
        className="text-[11px]"
        style={{
          color: "rgba(63,47,41,0.45)",
          fontFamily: "var(--font-sans)",
          letterSpacing: "0.06em",
        }}
      >
        {formatT(t)}
      </span>
    </div>
  )
}

/** Dotted-line divider drawn in the chat (manual cut / recall mark).
 *  No "processing" label — the gray-out state of the Send button is the
 *  only signal users need (per user feedback). */
function Divider({ kind, label }: { kind: "scissor" | "recall"; label?: string }) {
  if (label === "processing") return null
  const color = "rgba(63,47,41,0.32)"
  return (
    <div className="my-1.5 flex items-center gap-2">
      <div className="flex-1" style={{ borderTop: `1px dashed ${color}` }} />
      <span
        className="inline-flex items-center gap-1 text-[10.5px] tracking-wide uppercase"
        style={{ color, fontFamily: "var(--font-sans)" }}
      >
        {kind === "scissor" ? (
          <Scissors className="h-3 w-3" strokeWidth={1.6} />
        ) : (
          <RecallIcon className="h-3 w-3" strokeWidth={1.4} />
        )}
        <span>{label ?? (kind === "scissor" ? "sealed" : "recall")}</span>
      </span>
      <div className="flex-1" style={{ borderTop: `1px dashed ${color}` }} />
    </div>
  )
}

/** Send button — icon shoots up-right then resets, no opacity fade. */
function SendButton({ onSend, disabled }: { onSend: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onPointerDown={(e) => e.preventDefault()}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => {
        if (disabled) return
        onSend()
      }}
      disabled={disabled}
      className="grid h-9 w-9 place-items-center rounded-full transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
      style={{ color: "var(--color-cocoa)" }}
    >
      <Send className="h-5 w-5" strokeWidth={1.8} />
    </button>
  )
}

export default function App({ onBack }: { onBack?: () => void } = {}) {
  const [peerName, setPeerName] = useState(appConfig.aiName)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<{ id: string; file: File }[]>([])
  const sendBatchRef = useRef<{
    timer: number | null
    items: {
      text: string
      filesToSend: File[]
      recallUsed: boolean
    }[]
  }>({ timer: null, items: [] })
  const scrollRef = useRef<HTMLElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const cameraInputRef = useRef<HTMLInputElement | null>(null)
  const [attachOpen, setAttachOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const confirmTimerRef = useRef<number | null>(null)

  // Auto-scroll on new messages or text growth
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [messages])

  // Re-snap to bottom whenever the visual viewport resizes (soft keyboard
  // open/close). Otherwise the latest message hides under the composer pill.
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const onResize = () => {
      const el = scrollRef.current
      if (!el) return
      window.setTimeout(() => el.scrollTo({ top: el.scrollHeight, behavior: "smooth" }), 60)
    }
    vv.addEventListener("resize", onResize)
    return () => vv.removeEventListener("resize", onResize)
  }, [])

  // Autosize textarea (cap at ~3 lines, then scroll)
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = "auto"
    const lineHeight = 21 // 15px * 1.4
    const maxHeight = lineHeight * 3 + 8 // 3 lines + small padding
    const next = Math.min(ta.scrollHeight, maxHeight)
    ta.style.height = `${next}px`
    ta.style.overflowY = ta.scrollHeight > maxHeight ? "auto" : "hidden"
  }, [input])

  // Persist peer name edits to config
  useEffect(() => {
    if (peerName && peerName !== appConfig.aiName) saveConfig({ aiName: peerName })
  }, [peerName])

  // Re-read peer name when settings save it from elsewhere.
  useEffect(() => {
    function onConfigChanged() {
      setPeerName(appConfig.aiName)
    }
    window.addEventListener("favilla:config-changed", onConfigChanged)
    return () => window.removeEventListener("favilla:config-changed", onConfigChanged)
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchChatHistory("favilla")
      .then((res) => {
        if (cancelled || !res.ok || !res.messages) return
        setMessages(res.messages.map((msg) => ({
          ...msg,
          attachments: (msg.attachments || []).map((att) => {
            if (att.kind === "voice") return { kind: "voice", seconds: Number(att.size || 0) || 0 }
            if (att.kind === "image") return { kind: "image", name: att.name }
            return { kind: "file", name: att.name, size: att.size }
          }),
        })))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  // ---- manual cut + recall + process handlers ----
  // Scissor (剪刀) = cut. Drops a divider marker server-side (instant).
  // Hourglass single-tap = toggle recall armed (light up/off).
  //   When armed and the next message goes out, the server runs recall first.
  // Hourglass long-press 1.2s = confirm dialog → /api/app/process (DS pipeline).
  //   sealBusy=true for the whole DS round-trip; sand animation + Send disabled.
  const [sealBusy, setSealBusy] = useState(false)
  const [recallArmed, setRecallArmed] = useState(false)
  const [hourglassHold, setHourglassHold] = useState(0) // 0..1 long-press progress
  const hourglassTimerRef = useRef<number | null>(null)
  const hourglassStartRef = useRef<number>(0)
  const hourglassFiredRef = useRef(false) // true once long-press threshold reached
  const HOURGLASS_HOLD_MS = 1200
  const [confirmState, setConfirmState] = useState<
    | { open: false }
    | { open: true; title: string; message: string; confirmLabel?: string; onYes: () => void }
  >({ open: false })

  function pushDivider(kind: "scissor" | "recall", label?: string) {
    setMessages((m) => [
      ...m,
      { id: `div-${Date.now()}`, role: "ai", t: currentT(), divider: { kind, label } },
    ])
  }

  function askConfirm(title: string, message: string, onYes: () => void, confirmLabel?: string) {
    if (confirmTimerRef.current !== null) window.clearTimeout(confirmTimerRef.current)
    confirmTimerRef.current = null
    setConfirmState({ open: true, title, message, confirmLabel, onYes })
  }

  function onScissorClick() {
    if (sealBusy) return
    askConfirm(
      "Cut here?",
      "Add a cut marker here. The next process pass will split events at this point.",
      () => {
        pushDivider("scissor", "cut")
        cutFlow().catch(() => {})
      },
      "Cut",
    )
  }

  function runProcess() {
    if (sealBusy) return
    setSealBusy(true)
    processFlow()
      .catch(() => {})
      .finally(() => setSealBusy(false))
  }

  function clearHourglassHold() {
    if (hourglassTimerRef.current !== null) {
      window.cancelAnimationFrame(hourglassTimerRef.current)
      hourglassTimerRef.current = null
    }
    setHourglassHold(0)
  }

  function onHourglassPressStart(e?: React.PointerEvent<HTMLButtonElement>) {
    // Block default so the focused textarea doesn't lose focus (which would
    // dismiss the soft keyboard mid-press). preventDefault() on pointerdown
    // works for both mouse and touch in Android WebView when touch-action is
    // also constrained on the button.
    e?.preventDefault()
    if (sealBusy) return
    hourglassFiredRef.current = false
    hourglassStartRef.current = performance.now()
    const tick = () => {
      const elapsed = performance.now() - hourglassStartRef.current
      const p = Math.min(1, elapsed / HOURGLASS_HOLD_MS)
      setHourglassHold(p)
      if (p >= 1) {
        hourglassTimerRef.current = null
        hourglassFiredRef.current = true
        setHourglassHold(0)
        askConfirm(
          "Process unprocessed beats?",
          "The server will seal everything since the last process into events (using cut markers as segment dividers).",
          runProcess,
          "Process",
        )
        return
      }
      hourglassTimerRef.current = window.requestAnimationFrame(tick)
    }
    hourglassTimerRef.current = window.requestAnimationFrame(tick)
  }

  function onHourglassPressEnd() {
    const fired = hourglassFiredRef.current
    clearHourglassHold()
    if (sealBusy) return
    if (!fired) {
      // Short tap → toggle recall armed.
      setRecallArmed((on) => !on)
    }
  }

  useEffect(() => {
    return () => {
      const timer = sendBatchRef.current.timer
      if (timer !== null) window.clearTimeout(timer)
      if (confirmTimerRef.current !== null) window.clearTimeout(confirmTimerRef.current)
    }
  }, [])

  async function flushSendBatch() {
    const batch = sendBatchRef.current
    if (batch.timer !== null) {
      window.clearTimeout(batch.timer)
      batch.timer = null
    }
    const items = batch.items.splice(0)
    if (!items.length) return

    setSending(true)
    const aiId = `a-${Date.now()}`
    setMessages((m) => [...m, { id: aiId, role: "ai", t: currentT(), text: "" }])

    try {
      if (items.some((x) => x.recallUsed)) {
        try { await recallNow() } catch { /* non-fatal: chat still goes out */ }
      }

      const filesToSend = items.flatMap((x) => x.filesToSend)
      let attachments: ChatAttachment[] = []
      if (filesToSend.length > 0) {
        const up = await uploadFiles(filesToSend)
        if (!up.ok || !up.files) {
          setMessages((m) =>
            m.map((x) =>
              x.id === aiId
                ? { ...x, text: `upload failed: ${up.error || "unknown"}`, error: true }
                : x,
            ),
          )
          return
        }
        attachments = up.files
      }

      const combinedText = items.map((x) => x.text).filter(Boolean).join("\n\n")
      const res = await sendChat(combinedText || "(see attached file)", "favilla", attachments)
      if (!res.ok) {
        setMessages((m) =>
          m.map((x) =>
            x.id === aiId
              ? { ...x, text: `error: ${res.error || "unknown"}`, error: true }
              : x,
          ),
        )
        return
      }
      const thoughts: ThinkStep[] = (res.thoughts || []).map((t) => ({
        kind: t.kind || "think",
        text: t.text,
        result: t.result,
        source: t.source,
      }))
      const locked = !!res.thoughts_locked
      const full = res.reply || ""
      // Set the full reply at once. The previous typewriter re-rendered the
      // entire message list (with ReactMarkdown re-parsing) every 18ms,
      // which was the dominant cause of in-chat lag on the Android WebView.
      setMessages((m) =>
        m.map((x) =>
          x.id === aiId
            ? {
                ...x,
                text: full,
                thinking: thoughts.length > 0 ? thoughts : undefined,
                thinkingLocked: locked,
              }
            : x,
        ),
      )
      // Tell Shell to set the home unread dot if user isn't on chat.
      try {
        window.dispatchEvent(
          new CustomEvent("favilla:newAiReply", {
            detail: { peerName, preview: full },
          }),
        )
      } catch {
        /* ignore */
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setMessages((m) =>
        m.map((x) =>
          x.id === aiId ? { ...x, text: `network error: ${msg}`, error: true } : x,
        ),
      )
    } finally {
      setSending(false)
    }
  }

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const fs = Array.from(e.target.files || [])
    if (!fs.length) return
    setPendingFiles((cur) => [
      ...cur,
      ...fs.map((f) => ({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, file: f })),
    ])
    // Reset so selecting same file again re-triggers change
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  function removePending(id: string) {
    setPendingFiles((cur) => cur.filter((p) => p.id !== id))
  }

  async function handleSend() {
    const text = input.trim()
    if ((!text && pendingFiles.length === 0) || sending) return
    setInput("")
    const wasArmed = recallArmed
    if (wasArmed) {
      setRecallArmed(false)
    }

    // Snapshot pending files for this turn, then clear UI immediately
    const filesToSend = pendingFiles.map((p) => p.file)
    const userPills = pendingFiles.map((p) => {
      const isImage = p.file.type.startsWith("image/")
      return isImage
        ? ({ kind: "image", name: p.file.name } as const)
        : ({ kind: "file", name: p.file.name } as const)
    })
    setPendingFiles([])

    if (!text && filesToSend.length > 0) {
      setSending(true)
      try {
        const up = await uploadFiles(filesToSend)
        if (!up.ok || !up.files) {
          setMessages((m) => [...m, {
            id: `e-${Date.now()}`,
            role: "ai",
            t: currentT(),
            text: `upload failed: ${up.error || "unknown"}`,
            error: true,
          }])
          return
        }
        const uploadedPills = up.files.map((file) => (
          file.mime?.startsWith("image/")
            ? ({ kind: "image", name: file.name } as const)
            : ({ kind: "file", name: file.name, size: file.size } as const)
        ))
        const userMsg: Msg = {
          id: `u-${Date.now()}`,
          role: "user",
          t: currentT(),
          attachments: uploadedPills,
          recallUsed: wasArmed,
        }
        setMessages((m) => [...m, userMsg])
        await recordChatMessage({ role: "user", attachments: up.files.map((file) => ({
          kind: file.mime?.startsWith("image/") ? "image" : "file",
          name: file.name,
          path: file.path,
          mime: file.mime,
          size: file.size,
        })) })
      } finally {
        setSending(false)
      }
      return
    }

    const userMsg: Msg = {
      id: `u-${Date.now()}`,
      role: "user",
      t: currentT(),
      text,
      attachments: userPills.length > 0 ? userPills : undefined,
      recallUsed: wasArmed,
    }
    setMessages((m) => [...m, userMsg])

    const batch = sendBatchRef.current
    batch.items.push({ text, filesToSend, recallUsed: wasArmed })
    if (batch.timer !== null) window.clearTimeout(batch.timer)
    batch.timer = window.setTimeout(() => { void flushSendBatch() }, SEND_MERGE_WINDOW_MS)
  }

  function flushSendBatchNow() {
    if (sending || sendBatchRef.current.items.length === 0) return
    void flushSendBatch()
  }

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey) return
    e.preventDefault()
    if (input.trim() || pendingFiles.length > 0) {
      handleSend()
      return
    }
    flushSendBatchNow()
  }

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <div
        className="absolute inset-0 -z-10 bg-cover bg-center"
        style={{ backgroundImage: `url(${appConfig.bg})` }}
      />

      <div className="relative flex h-full flex-col">
          {/* nav bar — paddingTop pulls in safe-area inset on iOS/Android
              so the title doesn't sit behind a translucent status bar. */}
          <header
            className="flex shrink-0 items-center justify-between px-3"
            style={{
              background: "var(--color-cocoa)",
              color: "var(--color-cream)",
              minHeight: 56,
              paddingTop: "max(0px, env(safe-area-inset-top))",
            }}
          >
            <button
              type="button"
              onPointerDown={(e) => e.preventDefault()}
              onClick={onBack}
              className="grid h-10 w-10 place-items-center rounded-full hover:bg-white/10"
              aria-label="Back"
            >
              <ChevronLeft className="h-5 w-5" strokeWidth={1.8} />
            </button>
            <input
              value={peerName}
              onChange={(e) => setPeerName(e.target.value)}
              spellCheck={false}
              className="rounded bg-transparent px-2 py-0.5 text-center text-[15px] tracking-wide outline-none focus:bg-white/10"
              style={{
                fontFamily: "var(--font-sans)",
                color: "var(--color-cream)",
                width: "55%",
              }}
            />
            <button
              type="button"
              onPointerDown={(e) => e.preventDefault()}
              onClick={onScissorClick}
              className="grid h-10 w-10 place-items-center rounded-full hover:bg-white/10"
              aria-label="Cut"
            >
              <Scissors className="h-5 w-5" strokeWidth={1.8} />
            </button>
          </header>

          {/* messages — only the most recent 7 sealed blocks (cut-bounded) + live tail */}
          <main
            ref={scrollRef}
            className="flex flex-1 flex-col gap-[9px] overflow-y-auto px-4 pt-8 pb-36"
          >
            {(() => {
              const SHOW_BLOCKS = 7
              const cutIdxs: number[] = []
              messages.forEach((m, i) => { if (m.divider?.kind === "scissor") cutIdxs.push(i) })
              const drop = Math.max(0, cutIdxs.length - SHOW_BLOCKS)
              const startIdx = drop > 0 ? cutIdxs[drop - 1] + 1 : 0
              return messages.slice(startIdx)
            })().map((m, i, arr) => {
              const prev = arr[i - 1]
              const showTime = !prev || m.t - prev.t > 10
              const sameAuthorAsPrev =
                !!prev && prev.role === m.role && !showTime && !m.divider
              if (m.divider) {
                return (
                  <Divider key={m.id} kind={m.divider.kind} label={m.divider.label} />
                )
              }
              if (m.error) {
                return (
                  <div key={m.id} className="flex flex-col gap-[6px]">
                    {showTime && <TimeSeparator t={m.t} />}
                    <ErrorNote text={m.text || "unknown error"} />
                  </div>
                )
              }
              return (
                <div key={m.id} className="flex flex-col gap-[6px]">
                  {showTime && <TimeSeparator t={m.t} />}
                  <Bubble
                    msg={m}
                    peerName={peerName}
                    showName={!sameAuthorAsPrev}
                  />
                </div>
              )
            })}
            <div className="h-2" />
          </main>

          {/* composer — absolute floating pill, chat scrolls behind it.
              padding-bottom respects the bottom safe-area (gesture bar) so
              the pill never sits under transparent system nav. */}
          <footer
            className="pointer-events-none absolute inset-x-0 bottom-0 px-3 pt-2"
            style={{ paddingBottom: "max(16px, calc(env(safe-area-inset-bottom) + 8px))" }}
          >
            {pendingFiles.length > 0 && (
              <div className="pointer-events-auto mb-2 flex flex-wrap gap-1.5">
                {pendingFiles.map((p) => {
                  const isImage = p.file.type.startsWith("image/")
                  const Icon = isImage ? ImageIcon : FileText
                  const bgColor = isImage ? "rgba(199,195,176,0.85)" : "rgba(255,232,214,0.92)"
                  const iconColor = isImage ? "#5a5840" : "var(--color-cocoa)"
                  return (
                    <div
                      key={p.id}
                      className="inline-flex items-center gap-[3px] rounded-[6px] px-[5px] py-[2px]"
                      style={{ background: bgColor, width: 96 }}
                    >
                      <Icon
                        className="h-[14px] w-[14px] shrink-0"
                        strokeWidth={1.7}
                        style={{ color: iconColor }}
                      />
                      <span
                        className="min-w-0 flex-1 truncate text-[12px]"
                        style={{ color: INK, fontFamily: "var(--font-sans)", lineHeight: "1.3" }}
                      >
                        {p.file.name}
                      </span>
                      <button
                        type="button"
                        onClick={() => removePending(p.id)}
                        className="grid h-[14px] w-[14px] shrink-0 place-items-center rounded-full transition-opacity hover:opacity-70"
                        style={{ color: iconColor }}
                        aria-label="Remove attachment"
                      >
                        <X className="h-[10px] w-[10px]" strokeWidth={2.2} />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
            <div
              className="pointer-events-auto flex flex-col gap-1.5 rounded-[20px] p-1.5"
              onMouseDown={(e) => {
                // Don't blur the textarea when tapping anywhere inside the composer
                // (blank padding, tools row, etc.). The textarea itself ignores its
                // own mousedown so cursor placement still works.
                if (e.target !== textareaRef.current) e.preventDefault()
              }}
              style={{
                background: "rgba(255,250,240,0.98)",
                border: "1px solid rgba(176,139,127,0.22)",
                boxShadow:
                  "0 1px 0 rgba(255,255,255,0.7) inset, 0 10px 28px -14px rgba(102,78,68,0.4)",
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/jpeg,image/png,application/pdf,text/markdown,text/plain,application/zip"
                onChange={onPickFiles}
                style={{ display: "none" }}
              />
              <input
                ref={cameraInputRef}
                type="file"
                accept="image/*"
                capture="environment"
                onChange={onPickFiles}
                style={{ display: "none" }}
              />
              {/* input row (top) */}
              <textarea
                ref={textareaRef}
                rows={1}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onFocus={() => {
                  // When the soft keyboard pops up the viewport shrinks; give
                  // the layout a couple of frames then snap to the latest msg.
                  window.setTimeout(() => {
                    const el = scrollRef.current
                    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
                  }, 280)
                }}
                onKeyDown={onComposerKeyDown}
                placeholder={`Reply to ${peerName}…`}
                className="composer-input resize-none bg-transparent px-2 pt-2.5 pb-1 text-[15px] leading-[1.4] focus:outline-none"
                style={{ fontFamily: "var(--font-sans)", color: INK, maxHeight: 92, minHeight: 40 }}
              />
              {/* tools row (bottom): + / recall on left, voice / send on right */}
              <div className="flex items-center gap-1">
                <div className="relative">
                  <button
                    type="button"
                    onPointerDown={(e) => e.preventDefault()}
                    onClick={() => setAttachOpen((v) => !v)}
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-full hover:bg-black/5"
                    style={{ color: "var(--color-cocoa)" }}
                    aria-label="Attach"
                    aria-expanded={attachOpen}
                  >
                    <Plus className="h-5 w-5" strokeWidth={1.6} style={{
                      transform: attachOpen ? "rotate(45deg)" : "rotate(0)",
                      transition: "transform 160ms ease",
                    }} />
                  </button>
                  <AnimatePresence>
                    {attachOpen && (
                      <>
                        <button
                          type="button"
                          aria-label="Close attach menu"
                          onClick={() => setAttachOpen(false)}
                          className="fixed inset-0 z-10 cursor-default bg-transparent"
                        />
                        <motion.div
                          initial={{ opacity: 0, y: 8, scale: 0.96 }}
                          animate={{ opacity: 1, y: 0, scale: 1 }}
                          exit={{ opacity: 0, y: 6, scale: 0.96 }}
                          transition={{ duration: 0.14, ease: "easeOut" }}
                          className="absolute z-20 flex flex-col overflow-hidden rounded-2xl"
                          style={{
                            left: 0,
                            bottom: "calc(100% + 8px)",
                            minWidth: 134,
                            background: "rgba(255,250,243,0.96)",
                            backdropFilter: "blur(14px) saturate(110%)",
                            WebkitBackdropFilter: "blur(14px) saturate(110%)",
                            border: "1px solid rgba(176,139,127,0.22)",
                            boxShadow: "0 12px 28px -10px rgba(63,47,41,0.32)",
                          }}
                        >
                          <button
                            type="button"
                            onPointerDown={(e) => e.preventDefault()}
                            onClick={() => { setAttachOpen(false); cameraInputRef.current?.click() }}
                            className="flex items-center gap-2 px-3 py-2 text-left hover:bg-black/5"
                            style={{ color: INK, fontFamily: "var(--font-sans)" }}
                          >
                            <Camera className="h-[16px] w-[16px]" strokeWidth={1.6} />
                            <span className="text-[13px]">Take photo</span>
                          </button>
                          <div style={{ height: 1, background: "rgba(176,139,127,0.18)" }} />
                          <button
                            type="button"
                            onPointerDown={(e) => e.preventDefault()}
                            onClick={() => { setAttachOpen(false); fileInputRef.current?.click() }}
                            className="flex items-center gap-2 px-3 py-2 text-left hover:bg-black/5"
                            style={{ color: INK, fontFamily: "var(--font-sans)" }}
                          >
                            <ImageIcon className="h-[16px] w-[16px]" strokeWidth={1.6} />
                            <span className="text-[13px]">Upload file</span>
                          </button>
                        </motion.div>
                      </>
                    )}
                  </AnimatePresence>
                </div>
                <button
                  type="button"
                  onPointerDown={onHourglassPressStart}
                  onTouchStart={(e) => e.preventDefault()}
                  onPointerUp={onHourglassPressEnd}
                  onPointerLeave={() => clearHourglassHold()}
                  onPointerCancel={() => clearHourglassHold()}
                  disabled={sealBusy}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full transition-colors hover:bg-black/5 disabled:opacity-50"
                  style={{ color: "var(--color-cocoa)", touchAction: "none" }}
                  data-testid="hourglass"
                  aria-label={
                    sealBusy
                      ? "Processing event"
                      : recallArmed
                        ? "Recall armed (long-press to process)"
                        : "Tap to arm recall, long-press 1.2s to process"
                  }
                  aria-pressed={recallArmed}
                  title={
                    sealBusy
                      ? "Event is being processed…"
                      : recallArmed
                        ? "Recall armed — next message will refresh memory. Tap again to disarm."
                        : "Tap to arm recall · long-press 1.2s to process"
                  }
                >
                  <HourglassIcon
                    className="h-4 w-4"
                    size={16}
                    active={sealBusy}
                    filled={sealBusy || recallArmed || hourglassHold > 0}
                    fillProgress={
                      sealBusy ? 1 : hourglassHold > 0 ? hourglassHold : recallArmed ? 1 : 0
                    }
                    sandColor="#FAEC8C"
                    cycleSeconds={1}
                  />
                </button>
                {/* spacer */}
                <div className="flex-1" />
                <button
                  type="button"
                  onPointerDown={(e) => e.preventDefault()}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full"
                  style={{ color: "var(--color-cocoa)" }}
                  aria-label="Voice"
                >
                  <Mic className="h-5 w-5" strokeWidth={1.6} />
                </button>
                <SendButton
                  onSend={() => {
                    if (input.trim() || pendingFiles.length > 0) handleSend()
                    else flushSendBatchNow()
                  }}
                  disabled={sealBusy || sending || (!input.trim() && pendingFiles.length === 0 && sendBatchRef.current.items.length === 0)}
                />
              </div>
            </div>
          </footer>
          <ConfirmModal
            open={confirmState.open}
            title={confirmState.open ? confirmState.title : undefined}
            message={confirmState.open ? confirmState.message : undefined}
            confirmLabel={confirmState.open ? confirmState.confirmLabel : undefined}
            onCancel={() => setConfirmState({ open: false })}
            onConfirm={() => {
              if (confirmState.open) {
                const fn = confirmState.onYes
                setConfirmState({ open: false })
                fn()
              }
            }}
          />
      </div>
    </div>
  )
}
