import { useEffect, useMemo, useRef, useState } from "react"
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
} from "lucide-react"
import { LockIcon } from "./components/LockIcon"
import { RecallIcon } from "./components/RecallIcon"
import { HourglassIcon } from "./components/HourglassIcon"
import { ConfirmModal } from "./components/ConfirmModal"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { sendChat, uploadFiles, recallNow, sealEvent, type ChatAttachment } from "./lib/api"
import { appConfig, saveConfig } from "./config"

type Attachment =
  | { kind: "voice"; seconds: number }
  | { kind: "file"; name: string; size?: string }
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
  /** Minutes since epoch (mock). Used to decide whether to show a time separator. */
  t: number
  attachments?: Attachment[]
  thinking?: ThinkStep[]
  thinkingLocked?: boolean
  /** Non-bubble divider rendered in place of a chat bubble. */
  divider?: { kind: "scissor" | "recall"; label?: string }
  /** True if recall was armed when this user message was sent. Renders 🌠. */
  recallUsed?: boolean
}

const INK = "#3f2f29"

// Mock conversation seed. `t` in minutes (relative).
const seedMessages: Msg[] = [
  // 1: user voice (long-pressed → transcript shows below in same bubble area)
  {
    id: "1",
    role: "user",
    t: 0,
    attachments: [{ kind: "voice", seconds: 47 }],
    text:
      "*“And the city, in the end, was nothing more than a way of being alone with someone.”*",
  },
  // 2: ai reply
  {
    id: "2",
    role: "ai",
    t: 1,
    thinking: [
      { kind: "think", text: "Listening to the recording", result: "0:47 transcribed" },
      { kind: "search", text: "Looking up the passage in Calvino" },
      { kind: "check", text: "Done" },
    ],
    text:
      "I heard it. Your voice slowed on *alone with someone* — you held that part.\n\nIf solitude can be **shared**, what does the city give back to you tonight?",
  },
  // 3: user text + attachments below
  {
    id: "3",
    role: "user",
    t: 3,
    text: "Some pages from this morning. The window first, then the notes.",
    attachments: [
      { kind: "image", name: "morning-window.jpg" },
      { kind: "file", name: "calvino-cities.pdf" },
      { kind: "file", name: "notes-2026.md" },
      { kind: "image", name: "sunrise.png" },
    ],
  },
  // 4: ai reply
  {
    id: "4",
    role: "ai",
    t: 4,
    text:
      "The window is honest — it doesn't decide for you what to look at. **Read me one line** from the notes; I'll keep the rest for later.",
  },
]

function formatT(t: number) {
  const totalMin = (8 * 60 + 14 + t) % (24 * 60) // pretend day starts 08:14
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`
}

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

function Bubble({
  msg,
  idx,
  peerName,
  showName,
}: {
  msg: Msg
  idx: number
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
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.08 + idx * 0.05, duration: 0.4, ease: "easeOut" }}
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
            <NameTag>{isUser ? (appConfig.userName || "you").toLowerCase() : peerName.toLowerCase()}</NameTag>
          )}

        {!isUser && (msg.thinkingLocked || (msg.thinking && msg.thinking.length > 0)) && (
          <ThinkingChain steps={msg.thinking || []} locked={msg.thinkingLocked} peerName={peerName} />
        )}

        {voiceAttachments.length > 0 && (
          <Attachments list={voiceAttachments} isUser={isUser} />
        )}

        {msg.text && (
          <div
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
              backdropFilter: "blur(14px) saturate(120%)",
              WebkitBackdropFilter: "blur(14px) saturate(120%)",
              border: isUser
                ? "1px solid rgba(255,255,255,0.28)"
                : "1px solid rgba(255,255,255,0.5)",
              boxShadow:
                "0 1px 0 rgba(255,255,255,0.4) inset, 0 6px 20px -10px rgba(0,0,0,0.25)",
            }}
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
            {msg.recallUsed && (
              <span
                aria-label="recall used"
                className="pointer-events-none absolute -bottom-1 -right-1 select-none text-[14px] leading-none"
                style={{ filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.25))" }}
              >
                🌠
              </span>
            )}
          </div>
        )}

        {fileAttachments.length > 0 && (
          <Attachments list={fileAttachments} isUser={isUser} />
        )}
      </div>
    </motion.div>
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

/** Dotted-line divider drawn in the chat (manual cut / recall mark). */
function Divider({ kind, label }: { kind: "scissor" | "recall"; label?: string }) {
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
  const [shoot, setShoot] = useState(false)
  return (
    <button
      type="button"
      onClick={() => {
        if (disabled) return
        setShoot(true)
        onSend()
        setTimeout(() => setShoot(false), 220)
      }}
      disabled={disabled}
      className="grid h-9 w-9 place-items-center rounded-full overflow-hidden"
      style={{ color: "var(--color-cocoa)" }}
    >
      <motion.span
        animate={shoot ? { x: 16, y: -12, opacity: 0 } : { x: 0, y: 0, opacity: 1 }}
        transition={shoot ? { duration: 0.16, ease: [0.4, 0, 0.6, 1] } : { duration: 0 }}
        className="grid place-items-center"
      >
        <Send className="h-5 w-5" strokeWidth={1.8} />
      </motion.span>
    </button>
  )
}

export default function App({ onBack }: { onBack?: () => void } = {}) {
  const [peerName, setPeerName] = useState(appConfig.aiName)
  const [messages, setMessages] = useState<Msg[]>(seedMessages)
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<{ id: string; file: File }[]>([])
  const scrollRef = useRef<HTMLElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const cameraInputRef = useRef<HTMLInputElement | null>(null)
  const [attachOpen, setAttachOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const lastT = useMemo(() => messages[messages.length - 1]?.t ?? 0, [messages])

  // Auto-scroll on new messages or text growth
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
  }, [messages])

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

  // ---- manual cut + recall handlers ----
  // recallArmed: ON means the next outgoing message will trigger a recall
  //   refresh on the server before the AI replies.
  // sealBusy: ON while DS is processing a sealed event — recall is disabled
  //   in the meantime so we don't query a half-built memory.
  const [recallArmed, setRecallArmed] = useState(false)
  const [sealBusy, setSealBusy] = useState(false)
  const [confirmState, setConfirmState] = useState<
    | { open: false }
    | { open: true; title: string; message: string; onYes: () => void }
  >({ open: false })

  function pushDivider(kind: "scissor" | "recall", label?: string) {
    setMessages((m) => [
      ...m,
      { id: `div-${Date.now()}`, role: "ai", t: lastT + 1, divider: { kind, label } },
    ])
  }

  function askConfirm(title: string, message: string, onYes: () => void) {
    setConfirmState({ open: true, title, message, onYes })
  }

  function onScissorClick() {
    if (sealBusy) return
    askConfirm(
      "Seal this block?",
      "Everything since the last cut will be packaged into one event.",
      () => {
        pushDivider("scissor", "sealed")
        setSealBusy(true)
        sealEvent()
          .catch(() => {})
          .finally(() => setSealBusy(false))
      },
    )
  }

  function onRecallClick() {
    if (sealBusy) return // memory is being rebuilt; recall would be inconsistent
    // Arming is purely visual: marks intent that the next send should refresh
    // recall.md *before* sendChat. Tapping again disarms (mistap-safe).
    // The real network call to recallNow() happens in handleSend.
    setRecallArmed((on) => !on)
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
    setSending(true)
    // If recall is armed, refresh recall.md on the server FIRST so the AI
    // sees the freshest memory in this turn. Then disarm (toggle UX spec:
    // armed state only persists across taps, not across sends).
    const wasArmed = recallArmed
    if (wasArmed) {
      setRecallArmed(false)
      try { await recallNow() } catch { /* non-fatal: chat still goes out */ }
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

    const userMsg: Msg = {
      id: `u-${Date.now()}`,
      role: "user",
      t: lastT + 1,
      text,
      attachments: userPills.length > 0 ? userPills : undefined,
      recallUsed: wasArmed,
    }
    const aiId = `a-${Date.now()}`
    const aiMsg: Msg = { id: aiId, role: "ai", t: lastT + 1, text: "" }
    setMessages((m) => [...m, userMsg, aiMsg])

    try {
      let attachments: ChatAttachment[] = []
      if (filesToSend.length > 0) {
        const up = await uploadFiles(filesToSend)
        if (!up.ok || !up.files) {
          setMessages((m) =>
            m.map((x) =>
              x.id === aiId
                ? { ...x, text: `_(upload failed: ${up.error || "unknown"})_` }
                : x,
            ),
          )
          return
        }
        attachments = up.files
      }
      const res = await sendChat(text || "(see attached file)", "favilla", attachments)
      if (!res.ok) {
        setMessages((m) =>
          m.map((x) =>
            x.id === aiId
              ? { ...x, text: `_(error: ${res.error || "unknown"})_` }
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
      // Typewriter reveal of res.reply
      const full = res.reply || ""
      let i = 0
      const step = Math.max(1, Math.ceil(full.length / 120))
      await new Promise<void>((resolve) => {
        const tick = () => {
          i = Math.min(full.length, i + step)
          setMessages((m) =>
            m.map((x) =>
              x.id === aiId
                ? {
                    ...x,
                    text: full.slice(0, i),
                    thinking: thoughts.length > 0 ? thoughts : undefined,
                    thinkingLocked: locked,
                  }
                : x,
            ),
          )
          if (i < full.length) setTimeout(tick, 18)
          else resolve()
        }
        tick()
      })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setMessages((m) =>
        m.map((x) =>
          x.id === aiId ? { ...x, text: `_(network error: ${msg})_` } : x,
        ),
      )
    } finally {
      setSending(false)
    }
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
            className="flex flex-1 flex-col gap-[9px] overflow-y-auto px-4 pt-5 pb-24"
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
              return (
                <div key={m.id} className="flex flex-col gap-[6px]">
                  {showTime && <TimeSeparator t={m.t} />}
                  <Bubble
                    msg={m}
                    idx={i}
                    peerName={peerName}
                    showName={!sameAuthorAsPrev}
                  />
                </div>
              )
            })}
            <div className="h-2" />
          </main>

          {/* composer — absolute floating pill, chat scrolls behind it */}
          <footer className="pointer-events-none absolute inset-x-0 bottom-0 px-3 pb-4 pt-2">
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
              style={{
                background: "rgba(255,250,240,0.95)",
                backdropFilter: "blur(10px)",
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
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault()
                    handleSend()
                  }
                }}
                placeholder={`Reply to ${peerName}…`}
                className="composer-input resize-none bg-transparent px-2 py-1 text-[15px] leading-[1.4] focus:outline-none"
                style={{ fontFamily: "var(--font-sans)", color: INK, maxHeight: 71 }}
              />
              {/* tools row (bottom): + / recall on left, voice / send on right */}
              <div className="flex items-center gap-1">
                <div className="relative">
                  <button
                    type="button"
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
                            minWidth: 168,
                            background: "rgba(255,250,243,0.96)",
                            backdropFilter: "blur(14px) saturate(110%)",
                            WebkitBackdropFilter: "blur(14px) saturate(110%)",
                            border: "1px solid rgba(176,139,127,0.22)",
                            boxShadow: "0 12px 28px -10px rgba(63,47,41,0.32)",
                          }}
                        >
                          <button
                            type="button"
                            onClick={() => { setAttachOpen(false); cameraInputRef.current?.click() }}
                            className="flex items-center gap-3 px-4 py-3 text-left hover:bg-black/5"
                            style={{ color: INK, fontFamily: "var(--font-sans)" }}
                          >
                            <Camera className="h-[18px] w-[18px]" strokeWidth={1.6} />
                            <span className="text-[14px]">Take photo</span>
                          </button>
                          <div style={{ height: 1, background: "rgba(176,139,127,0.18)" }} />
                          <button
                            type="button"
                            onClick={() => { setAttachOpen(false); fileInputRef.current?.click() }}
                            className="flex items-center gap-3 px-4 py-3 text-left hover:bg-black/5"
                            style={{ color: INK, fontFamily: "var(--font-sans)" }}
                          >
                            <ImageIcon className="h-[18px] w-[18px]" strokeWidth={1.6} />
                            <span className="text-[14px]">Album / file</span>
                          </button>
                        </motion.div>
                      </>
                    )}
                  </AnimatePresence>
                </div>
                <button
                  type="button"
                  onClick={onRecallClick}
                  disabled={sealBusy}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full transition-colors hover:bg-black/5 disabled:opacity-50"
                  style={{ color: "var(--color-cocoa)" }}
                  aria-label={sealBusy ? "Processing event" : recallArmed ? "Recall armed" : "Recall"}
                  aria-pressed={recallArmed}
                  title={
                    sealBusy
                      ? "Event is being processed — recall paused"
                      : recallArmed
                        ? "Recall armed: next message will refresh memory"
                        : "Tap to arm recall for the next message"
                  }
                >
                  {sealBusy ? (
                    <HourglassIcon className="h-[14px] w-[14px]" active />
                  ) : (
                    <RecallIcon
                      className="h-[14px] w-[14px]"
                      strokeWidth={1.2}
                      fill={recallArmed ? "#FAEC8C" : "none"}
                    />
                  )}
                </button>
                {/* spacer */}
                <div className="flex-1" />
                <button
                  type="button"
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full"
                  style={{ color: "var(--color-cocoa)" }}
                  aria-label="Voice"
                >
                  <Mic className="h-5 w-5" strokeWidth={1.6} />
                </button>
                <SendButton onSend={handleSend} disabled={sending || (!input.trim() && pendingFiles.length === 0)} />
              </div>
            </div>
          </footer>
          <ConfirmModal
            open={confirmState.open}
            title={confirmState.open ? confirmState.title : undefined}
            message={confirmState.open ? confirmState.message : undefined}
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
