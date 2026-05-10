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
  Share2,
} from "lucide-react"
import { DynamicIcon, iconNames, type IconName } from "lucide-react/dynamic"
import { LockIcon } from "./components/LockIcon"
import { RecallIcon } from "./components/RecallIcon"
import { HourglassIcon } from "./components/HourglassIcon"
import { ConfirmModal } from "./components/ConfirmModal"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { fetchChatTranscript, recordChatMessage, sendChat, uploadFiles, recallNow, cutFlow, processFlow, type ChatAttachment, type ChatSegment } from "./lib/api"
import { useComputerStatus, describeEvent } from "./lib/computerStatus"
import { appConfig } from "./config"

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
  summary?: string
  result?: string
  source?: "marker" | "native"
  locked?: boolean
  icon?: string
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
  segments?: ChatSegment[]
  hold?: { queued?: number; immediate?: boolean }
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

function wrapCanvasText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number) {
  const lines: string[] = []
  for (const paragraph of text.replace(/\r/g, "").split("\n")) {
    if (!paragraph) {
      lines.push("")
      continue
    }
    let line = ""
    for (const char of Array.from(paragraph)) {
      const next = line + char
      if (line && ctx.measureText(next).width > maxWidth) {
        lines.push(line)
        line = char
      } else {
        line = next
      }
    }
    if (line) lines.push(line)
  }
  return lines
}

function attachmentLine(a: Attachment) {
  if (a.kind === "voice") return `voice 0:${String(a.seconds).padStart(2, "0")}`
  if (a.kind === "image") return `image ${a.name}`
  return `file ${a.name}`
}

function drawRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

async function makeShareImage(selected: Msg[], peerName: string) {
  if (document.fonts?.ready) await document.fonts.ready.catch(() => undefined)
  const width = 720
  const pad = 36
  const maxBubble = 510
  const tmp = document.createElement("canvas")
  const measure = tmp.getContext("2d")!
  const sans = "Anthropic Sans, Inter, system-ui, sans-serif"
  const serif = "Anthropic Serif, Georgia, serif"
  const mono = "Anthropic Mono, ui-monospace, monospace"

  type Block = {
    msg: Msg
    name: string
    isUser: boolean
    thinking: string[]
    body: string[]
    width: number
    height: number
  }
  const blocks: Block[] = selected.map((msg) => {
    const isUser = msg.role === "user"
    const name = isUser ? (appConfig.userName || "you") : peerName
    const thinkingText = !isUser
      ? msg.thinkingLocked
        ? [`${peerName} thought silently`]
        : (msg.thinking || []).flatMap((step) => [step.text, step.result || ""].filter(Boolean))
      : []
    measure.font = `24px ${sans}`
    const thinking = thinkingText.flatMap((text) => wrapCanvasText(measure, text, maxBubble - 36))
    measure.font = `29px ${sans}`
    const bodyText = [msg.text || "", ...(msg.attachments || []).map(attachmentLine)].filter(Boolean).join("\n")
    const body = wrapCanvasText(measure, bodyText || " ", maxBubble - 42)
    const bodyWidth = Math.min(
      maxBubble,
      Math.max(170, ...body.map((line) => measure.measureText(line || " ").width + 42)),
    )
    const thinkingWidth = thinking.length
      ? Math.min(maxBubble, Math.max(220, ...thinking.map((line) => measure.measureText(line || " ").width + 36)))
      : 0
    const blockWidth = Math.max(bodyWidth, thinkingWidth)
    const thinkingHeight = thinking.length ? thinking.length * 29 + 28 : 0
    const bodyHeight = body.length * 34 + 30
    return {
      msg,
      name,
      isUser,
      thinking,
      body,
      width: blockWidth,
      height: 30 + thinkingHeight + bodyHeight + 26,
    }
  })
  const height = Math.max(360, 62 + blocks.reduce((sum, block) => sum + block.height, 0) + 34)
  const scale = 2
  const canvas = document.createElement("canvas")
  canvas.width = width * scale
  canvas.height = height * scale
  const ctx = canvas.getContext("2d")!
  ctx.scale(scale, scale)
  ctx.fillStyle = "#f7efe2"
  ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = "rgba(176,139,127,0.10)"
  for (let y = 22; y < height; y += 36) {
    for (let x = 22; x < width; x += 36) {
      ctx.beginPath()
      ctx.arc(x, y, 1.1, 0, Math.PI * 2)
      ctx.fill()
    }
  }
  ctx.font = `600 24px ${serif}`
  ctx.fillStyle = INK
  ctx.fillText("Favilla", pad, 42)
  let y = 72
  for (const block of blocks) {
    const x = block.isUser ? width - pad - block.width : pad
    ctx.font = `italic 600 20px ${serif}`
    ctx.fillStyle = "rgba(63,47,41,0.70)"
    ctx.textAlign = block.isUser ? "right" : "left"
    ctx.fillText(block.name, block.isUser ? x + block.width - 4 : x + 4, y)
    y += 12
    if (block.thinking.length) {
      const h = block.thinking.length * 29 + 20
      drawRoundRect(ctx, x, y, block.width, h, 16)
      ctx.fillStyle = "rgba(255,250,240,0.72)"
      ctx.fill()
      ctx.strokeStyle = "rgba(176,139,127,0.24)"
      ctx.stroke()
      ctx.textAlign = "left"
      ctx.font = `22px ${sans}`
      ctx.fillStyle = "rgba(63,47,41,0.66)"
      let ty = y + 29
      for (const line of block.thinking) {
        ctx.fillText(line || " ", x + 18, ty)
        ty += 29
      }
      y += h + 8
    }
    const bodyHeight = block.body.length * 34 + 30
    drawRoundRect(ctx, x, y, block.width, bodyHeight, 22)
    ctx.fillStyle = block.isUser ? "rgba(208,188,190,0.82)" : "rgba(235,235,235,0.78)"
    ctx.fill()
    ctx.strokeStyle = "rgba(255,255,255,0.58)"
    ctx.stroke()
    ctx.textAlign = "left"
    ctx.font = `29px ${sans}`
    ctx.fillStyle = INK
    let by = y + 39
    for (const line of block.body) {
      ctx.fillText(line || " ", x + 21, by)
      by += 34
    }
    y += bodyHeight + 26
  }
  ctx.textAlign = "right"
  ctx.font = `18px ${mono}`
  ctx.fillStyle = "rgba(63,47,41,0.38)"
  ctx.fillText(new Date().toLocaleString(), width - pad, height - 22)
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("image export failed"))), "image/png")
  })
  return blob
}

async function shareBlob(blob: Blob) {
  const file = new File([blob], `favilla-chat-${Date.now()}.png`, { type: "image/png" })
  const nav = navigator as Navigator & {
    canShare?: (data: ShareData) => boolean
    share?: (data: ShareData) => Promise<void>
  }
  if (nav.share && (!nav.canShare || nav.canShare({ files: [file] }))) {
    await nav.share({ files: [file], title: "Favilla chat" })
    return
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = file.name
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
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
const LUCIDE_ICON_NAMES = new Set<string>(iconNames)
const STREAMLINE_THINK_ICONS = new Set([
  "attachment",
  "bookmark",
  "bookmark-tag",
  "calendar",
  "calendar-check",
  "calendar-heart",
  "chat-message",
  "clipboard",
  "clipboard-check",
  "clock",
  "dashboard",
  "dashboard-gauge",
  "download",
  "file-text",
  "folder",
  "folder-add",
  "fountain-pen",
  "heart",
  "home",
  "home-simple",
  "menu",
  "menu-dots",
  "note",
  "pen",
  "pencil",
  "pin",
  "quill",
  "settings",
  "share",
  "sliders",
  "user-profile",
  "write",
  "write-paper",
])

const EXPLICIT_STREAMLINE_ICON: Record<string, string> = {
  alarmclock: "clock",
  bookmark: "bookmark",
  calendar: "calendar",
  calendarcheck: "calendar-check",
  check: "clipboard-check",
  checkcircle: "clipboard-check",
  checkcircle2: "clipboard-check",
  circlecheck: "clipboard-check",
  clipboard: "clipboard",
  clipboardcheck: "clipboard-check",
  clock: "clock",
  clock3: "clock",
  download: "download",
  edit: "write-paper",
  file: "file-text",
  filetext: "file-text",
  folder: "folder",
  folderplus: "folder-add",
  heart: "heart",
  home: "home",
  locatefixed: "pin",
  mappin: "pin",
  messagecircle: "chat-message",
  messagesquare: "chat-message",
  note: "note",
  notebook: "note",
  paperclip: "attachment",
  pen: "pen",
  pencil: "pencil",
  pin: "pin",
  searchcheck: "clipboard-check",
  sendto: "share",
  settings: "settings",
  share: "share",
  share2: "share",
  slidershorizontal: "sliders",
  squarepen: "write-paper",
  user: "user-profile",
  userround: "user-profile",
  wrench: "settings",
}

const STREAMLINE_KEYWORD_RULES: Array<{ pattern: RegExp; slug: string }> = [
  { pattern: /\b(read|open|file|document|text|markdown|json|csv|log)\b|文件|文档|日志|读取|查看/, slug: "file-text" },
  { pattern: /\b(list|dir|folder|tree|workspace)\b|目录|文件夹|列表/, slug: "folder" },
  { pattern: /\b(grep|search|find|query|lookup|scan)\b|搜索|检索|查找|寻找/, slug: "file-text" },
  { pattern: /\b(write|edit|patch|update|modify|note|draft)\b|写|编辑|修改|记录|笔记|草稿/, slug: "write-paper" },
  { pattern: /\b(todo|task|plan|checklist|queue|verify|test|build|pass|done)\b|待办|任务|计划|清单|验证|测试|构建|完成/, slug: "clipboard-check" },
  { pattern: /\b(time|clock|later|hold|wait|sleep|calendar|date)\b|时间|稍后|等待|日历|提醒/, slug: "clock" },
  { pattern: /\b(config|setting|preference|option|control)\b|设置|配置|选项|控制/, slug: "settings" },
  { pattern: /\b(api|dashboard|status|health|metric)\b|状态|面板|仪表/, slug: "dashboard-gauge" },
  { pattern: /\b(upload|attach|attachment|image|photo)\b|上传|附件|图片|照片/, slug: "attachment" },
  { pattern: /\b(stroll|map|marker|pin|place|nearby|location|coordinate)\b|地图|标记|地点|附近|位置|坐标/, slug: "pin" },
  { pattern: /\b(share|export|handoff|carry)\b|分享|导出|转交|交接/, slug: "share" },
  { pattern: /\b(chat|message|reply|conversation)\b|聊天|消息|回复|对话/, slug: "chat-message" },
  { pattern: /\b(memory|favorite|save|bookmark)\b|记忆|收藏|保存/, slug: "bookmark" },
]

function dynamicIconName(icon?: string): IconName | "" {
  const clean = (icon || "").replace(/[^A-Za-z0-9]/g, "")
  if (!clean) return ""
  const kebab = clean
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .replace(/([A-Za-z])([0-9])/g, "$1-$2")
    .toLowerCase()
  return LUCIDE_ICON_NAMES.has(kebab) ? (kebab as IconName) : ""
}

function compactIconKey(value?: string) {
  return (value || "").replace(/[^A-Za-z0-9]/g, "").toLowerCase()
}

function inferStreamlineIcon(step: ThinkStep): string {
  // 1. explicit icon hint from the step → highest priority
  const explicit = EXPLICIT_STREAMLINE_ICON[compactIconKey(step.icon)]
  if (explicit && STREAMLINE_THINK_ICONS.has(explicit)) return explicit
  // 2. pure "thinking" steps (CC monologue, no tool call, no icon) MUST fall
  //    back to the Brain fallback. The keyword rules historically swept words
  //    like "file" / "text" out of the prose and produced a misleading
  //    file-text icon, which is what users complained about. Only match
  //    keywords against the icon hint and the explicit source label, never
  //    the free-form text/summary.
  const haystack = [step.icon, step.source].filter(Boolean).join(" ").toLowerCase()
  if (haystack) {
    for (const rule of STREAMLINE_KEYWORD_RULES) {
      if (rule.pattern.test(haystack) && STREAMLINE_THINK_ICONS.has(rule.slug)) return rule.slug
    }
  }
  // 3. native tool calls without a more specific signal → settings cog
  if (step.source === "native" && step.kind !== "think") return "settings"
  return ""
}

function fallbackThinkIcon(step: ThinkStep) {
  const haystack = [step.icon, step.summary, step.text, step.result].filter(Boolean).join(" ").toLowerCase()
  if (step.kind === "search" || /\b(grep|search|find|query|lookup|scan)\b|搜索|检索|查找|寻找/.test(haystack)) return Search
  if (step.kind === "check" || /\b(check|verify|test|build|pass|done)\b|检查|验证|测试|构建|完成/.test(haystack)) return CheckCircle2
  return Brain
}

function StreamlineThinkIcon({ slug }: { slug: string }) {
  const url = `/icons/streamline/${slug}.svg`
  return (
    <span
      aria-hidden="true"
      className="block h-3.5 w-3.5"
      style={{
        backgroundColor: "currentColor",
        maskImage: `url(${url})`,
        maskPosition: "center",
        maskRepeat: "no-repeat",
        maskSize: "contain",
        WebkitMaskImage: `url(${url})`,
        WebkitMaskPosition: "center",
        WebkitMaskRepeat: "no-repeat",
        WebkitMaskSize: "contain",
      }}
    />
  )
}

function ThinkIcon({ step }: { step: ThinkStep }) {
  const streamlineSlug = inferStreamlineIcon(step)
  if (streamlineSlug) return <StreamlineThinkIcon slug={streamlineSlug} />
  const dynamicName = dynamicIconName(step.icon)
  const Fallback = fallbackThinkIcon(step)
  const fallback = <Fallback className="h-3.5 w-3.5" strokeWidth={1.6} />
  if (!dynamicName) return fallback
  return <DynamicIcon name={dynamicName} className="h-3.5 w-3.5" strokeWidth={1.6} fallback={() => fallback} />
}

function ThinkingChain({ steps, locked, peerName }: { steps: ThinkStep[]; locked?: boolean; peerName?: string }) {
  const [open, setOpen] = useState(false)
  const summary = steps.find((step) => step.summary || step.text)?.summary || steps.find((step) => step.text)?.text
  if (locked) {
    return (
      <div className="w-full">
        <div
          className="mb-2 inline-flex items-center gap-1 text-[12px]"
          style={{ color: "rgba(63,47,41,0.45)", fontFamily: "var(--font-sans)" }}
        >
          <span>{summary || `${peerName || "AI"} thought silently`}</span>
          <LockIcon className="h-3.5 w-3" strokeWidth={1} />
        </div>
      </div>
    )
  }
  return (
    <div className="w-full">
      <button
        type="button"
        onPointerDown={(e) => {
          e.preventDefault()
          e.stopPropagation()
        }}
        onClick={() => setOpen((v) => !v)}
        className="mb-2 inline-flex items-center gap-1 text-[12px]"
        style={{
          color: "rgba(63,47,41,0.55)",
          fontFamily: "var(--font-sans)",
        }}
      >
        <span>{open ? "Hide thinking" : (summary || "Show thinking")}</span>
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
                        <ThinkIcon step={s} />
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

const USER_BUBBLE_BG = "rgba(208,188,190,0.72)"
const AGENT_BUBBLE_BG = "rgba(235,235,235,0.62)"

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

function stepFromSegment(segment: Extract<ChatSegment, { type: "thought" }>): ThinkStep {
  return {
    kind: segment.kind || "think",
    text: segment.text || segment.summary || "",
    summary: segment.summary,
    result: segment.result,
    source: segment.source,
    locked: segment.locked,
    icon: segment.icon,
  }
}

function Bubble({
  msg,
  peerName,
  showName,
  selectionMode,
  selected,
  onSelect,
  onLongSelect,
}: {
  msg: Msg
  peerName: string
  showName: boolean
  selectionMode?: boolean
  selected?: boolean
  onSelect?: () => void
  onLongSelect?: () => void
}) {
  const isUser = msg.role === "user"
  const voiceAttachments = (msg.attachments ?? []).filter(
    (a) => a.kind === "voice",
  )
  const fileAttachments = (msg.attachments ?? []).filter(
    (a) => a.kind !== "voice",
  )
  const orderedSegments = !isUser ? (msg.segments || []).filter((segment) => {
    if (segment.type === "text") return !!segment.text
    return !!(segment.text || segment.summary)
  }) : []
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
            orderedSegments.length > 0 ||
            (msg.thinking?.length ?? 0) > 0 ||
            (msg.attachments?.length ?? 0) > 0) && (
            <NameTag>{isUser ? (appConfig.userName || "you") : peerName}</NameTag>
          )}

        {!isUser && orderedSegments.length === 0 && (msg.thinkingLocked || (msg.thinking && msg.thinking.length > 0)) && (
          <ThinkingChain steps={msg.thinking || []} locked={msg.thinkingLocked} peerName={peerName} />
        )}

        {voiceAttachments.length > 0 && (
          <Attachments list={voiceAttachments} isUser={isUser} />
        )}

        {orderedSegments.length > 0 ? (
          orderedSegments.map((segment, index) =>
            segment.type === "text" ? (
              <BubbleBody
                key={`${msg.id}-seg-${index}`}
                text={segment.text}
                isUser={isUser}
                recallUsed={!!msg.recallUsed && index === 0}
                selectionMode={!!selectionMode}
                selected={!!selected}
                onSelect={onSelect}
                onLongSelect={onLongSelect}
              />
            ) : (
              <ThinkingChain
                key={`${msg.id}-seg-${index}`}
                steps={[stepFromSegment(segment)]}
                locked={msg.thinkingLocked || !!segment.locked}
                peerName={peerName}
              />
            ),
          )
        ) : msg.text ? (
          <BubbleBody
            text={msg.text}
            isUser={isUser}
            recallUsed={!!msg.recallUsed}
            selectionMode={!!selectionMode}
            selected={!!selected}
            onSelect={onSelect}
            onLongSelect={onLongSelect}
          />
        ) : null}

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
  selectionMode,
  selected,
  onSelect,
  onLongSelect,
}: {
  text: string
  isUser: boolean
  recallUsed: boolean
  selectionMode: boolean
  selected: boolean
  onSelect?: () => void
  onLongSelect?: () => void
}) {
  const [showCopy, setShowCopy] = useState(false)
  const [copied, setCopied] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const longPressTimerRef = useRef<number | null>(null)
  const longPressFiredRef = useRef(false)
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
  useEffect(() => () => clearLongPress(), [])
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
  function clearLongPress() {
    if (longPressTimerRef.current !== null) {
      window.clearTimeout(longPressTimerRef.current)
      longPressTimerRef.current = null
    }
  }
  return (
    <div ref={wrapRef} className="relative inline-block" style={{ overflow: "visible" }}>
      <div
        onPointerDown={() => {
          longPressFiredRef.current = false
          if (!onLongSelect) return
          clearLongPress()
          longPressTimerRef.current = window.setTimeout(() => {
            longPressTimerRef.current = null
            longPressFiredRef.current = true
            setShowCopy(false)
            onLongSelect()
          }, 460)
        }}
        onPointerUp={clearLongPress}
        onPointerCancel={clearLongPress}
        onPointerLeave={clearLongPress}
        onContextMenu={(e) => e.preventDefault()}
        onClick={(e) => {
          if (longPressFiredRef.current) {
            longPressFiredRef.current = false
            e.preventDefault()
            return
          }
          if (selectionMode) {
            e.preventDefault()
            setShowCopy(false)
            onSelect?.()
            return
          }
          setShowCopy((v) => !v)
        }}
        className={`md relative px-4 py-3 text-[14.5px] leading-[1.6] ${
          isUser
            ? "rounded-[18px] rounded-br-[6px]"
            : "rounded-[18px] rounded-bl-[6px]"
        }`}
        style={{
          background: isUser ? USER_BUBBLE_BG : AGENT_BUBBLE_BG,
          color: INK,
          border: isUser
            ? "1px solid rgba(255,255,255,0.28)"
            : "1px solid rgba(255,255,255,0.5)",
          outline: selected ? "2px solid rgba(122,138,82,0.76)" : "none",
          outlineOffset: 3,
          boxShadow:
            "0 1px 0 rgba(255,255,255,0.4) inset, 0 6px 20px -10px rgba(0,0,0,0.25)",
          cursor: "pointer",
        }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        {selected && (
          <span
            className="absolute -top-2 grid h-5 w-5 place-items-center rounded-full"
            style={{
              [isUser ? "left" : "right"]: -6,
              background: "#7a8a52",
              color: "#fffaf0",
              boxShadow: "0 2px 8px rgba(63,47,41,0.25)",
            }}
            aria-hidden="true"
          >
            <Check className="h-3 w-3" strokeWidth={2.4} />
          </span>
        )}
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
        {showCopy && !selectionMode && (
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
function SendButton({
  onSend,
  disabled,
  onStablePointerDown,
}: {
  onSend: () => void
  disabled: boolean
  onStablePointerDown: (e: React.PointerEvent<HTMLElement>) => void
}) {
  return (
    <button
      type="button"
      onPointerDown={onStablePointerDown}
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
  const peerName = appConfig.aiName || "ai"
  const computerStatus = useComputerStatus()
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [sharing, setSharing] = useState(false)
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
  const composerRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const cameraInputRef = useRef<HTMLInputElement | null>(null)
  const [attachOpen, setAttachOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const confirmTimerRef = useRef<number | null>(null)
  const maxViewportHeightRef = useRef(0)
  const stickToBottomRef = useRef(true)

  function blurActiveInput() {
    const active = document.activeElement
    if (active instanceof HTMLElement && active !== document.body) active.blur()
  }

  function isKeyboardVisibleNow() {
    if (typeof window === "undefined") return false
    const vv = window.visualViewport
    const current = vv?.height || window.innerHeight
    maxViewportHeightRef.current = Math.max(maxViewportHeightRef.current, current, window.innerHeight)
    return maxViewportHeightRef.current - current > 90
  }

  useEffect(() => {
    function releaseStaleComposerFocus(e: PointerEvent) {
      if (e.target === textareaRef.current) return
      if (document.activeElement === textareaRef.current && !isKeyboardVisibleNow()) {
        textareaRef.current?.blur()
      }
    }
    document.addEventListener("pointerdown", releaseStaleComposerFocus, true)
    return () => document.removeEventListener("pointerdown", releaseStaleComposerFocus, true)
  }, [])

  function onStableControlPointerDown(e: React.PointerEvent<HTMLElement>) {
    e.preventDefault()
    e.stopPropagation()
  }

  function isNearBottom(el: HTMLElement) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 140
  }

  function rememberScrollPin() {
    const el = scrollRef.current
    if (el) stickToBottomRef.current = isNearBottom(el)
  }

  function scrollToBottom(behavior: ScrollBehavior = "auto") {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior })
  }

  function exitChat() {
    setAttachOpen(false)
    setSelectedIds([])
    blurActiveInput()
    onBack?.()
  }

  function startSelecting(id: string) {
    setAttachOpen(false)
    setSelectedIds((cur) => (cur.includes(id) ? cur : [...cur, id]))
  }

  function toggleSelected(id: string) {
    setSelectedIds((cur) => {
      if (cur.includes(id)) return cur.filter((x) => x !== id)
      return [...cur, id]
    })
  }

  async function shareSelectedMessages() {
    const selected = messages.filter((m) => selectedIds.includes(m.id) && !m.divider && !m.error)
    if (!selected.length || sharing) return
    setSharing(true)
    try {
      const blob = await makeShareImage(selected, peerName)
      await shareBlob(blob)
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return
      // eslint-disable-next-line no-console
      console.warn("share image failed", e)
    } finally {
      setSharing(false)
    }
  }

  function streamReply(aiId: string, full: string, thoughts: ThinkStep[], locked: boolean, segments?: ChatSegment[], hold?: Msg["hold"]) {
    if (segments && segments.length > 0) {
      setMessages((m) =>
        m.map((x) =>
          x.id === aiId
            ? {
                ...x,
                text: full,
                thinking: thoughts.length > 0 ? thoughts : undefined,
                thinkingLocked: locked,
                segments,
                hold,
              }
            : x,
        ),
      )
      return
    }
    if (!full) {
      setMessages((m) =>
        m.map((x) =>
          x.id === aiId
            ? { ...x, text: full, thinking: thoughts.length > 0 ? thoughts : undefined, thinkingLocked: locked, hold }
            : x,
        ),
      )
      return
    }
    const chars = Array.from(full)
    let index = 0
    const chunk = chars.length < 140 ? 1 : chars.length < 520 ? 3 : 5
    const tick = () => {
      index = Math.min(chars.length, index + chunk)
      const shown = chars.slice(0, index).join("")
      setMessages((m) =>
        m.map((x) =>
          x.id === aiId
            ? {
                ...x,
                text: shown,
                thinking: thoughts.length > 0 ? thoughts : undefined,
                thinkingLocked: locked,
                hold,
              }
            : x,
        ),
      )
      if (index < chars.length) window.setTimeout(tick, 42)
    }
    tick()
  }

  // Auto-scroll only while the user is reading the live tail.
  useEffect(() => {
    if (stickToBottomRef.current) scrollToBottom("auto")
  }, [messages])

  // Re-snap to bottom whenever the visual viewport resizes (soft keyboard
  // open/close). Otherwise the latest message hides under the composer pill.
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const onResize = () => {
      maxViewportHeightRef.current = Math.max(maxViewportHeightRef.current, vv.height, window.innerHeight)
      if (stickToBottomRef.current) window.setTimeout(() => scrollToBottom("auto"), 60)
    }
    onResize()
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

  useEffect(() => {
    let cancelled = false
    fetchChatTranscript("chat")
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
  // Hourglass long-press 1.2s = confirm dialog -> /favilla/chat/process (DS pipeline).
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
    e?.stopPropagation()
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
    stickToBottomRef.current = true
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
      const res = await sendChat(combinedText || "(see attached file)", "chat", attachments)
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
        summary: t.summary,
        result: t.result,
        source: t.source,
        locked: t.locked,
        icon: t.icon,
      }))
      const locked = !!res.thoughts_locked
      const full = res.reply || ""
      streamReply(aiId, full, thoughts, locked, res.segments, res.hold)
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
        stickToBottomRef.current = true
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
    stickToBottomRef.current = true
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
            {selectedIds.length > 0 ? (
              <>
                <button
                  type="button"
                  onPointerDown={onStableControlPointerDown}
                  onClick={() => setSelectedIds([])}
                  className="grid h-10 w-10 place-items-center rounded-full hover:bg-white/10"
                  aria-label="Cancel selection"
                >
                  <X className="h-5 w-5" strokeWidth={1.8} />
                </button>
                <div
                  className="text-center text-[15px] tracking-wide"
                  style={{ fontFamily: "var(--font-sans)", color: "var(--color-cream)", width: "55%" }}
                >
                  {selectedIds.length} selected
                </div>
                <button
                  type="button"
                  onPointerDown={onStableControlPointerDown}
                  onClick={shareSelectedMessages}
                  disabled={sharing}
                  className="grid h-10 w-10 place-items-center rounded-full hover:bg-white/10 disabled:opacity-50"
                  aria-label="Share selected messages"
                >
                  <Share2 className="h-5 w-5" strokeWidth={1.8} />
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onPointerDown={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                  }}
                  onClick={exitChat}
                  className="grid h-10 w-10 place-items-center rounded-full hover:bg-white/10"
                  aria-label="Back"
                >
                  <ChevronLeft className="h-5 w-5" strokeWidth={1.8} />
                </button>
                <span
                  className="px-2 py-0.5 text-center text-[15px] tracking-wide"
                  style={{
                    fontFamily: "var(--font-sans)",
                    color: "var(--color-cream)",
                    width: "55%",
                  }}
                >
                  {peerName}
                </span>
                <button
                  type="button"
                  onPointerDown={onStableControlPointerDown}
                  onClick={onScissorClick}
                  className="grid h-10 w-10 place-items-center rounded-full hover:bg-white/10"
                  aria-label="Cut"
                >
                  <Scissors className="h-5 w-5" strokeWidth={1.8} />
                </button>
              </>
            )}
          </header>

          {/* live computer-control status — pushed via SSE, no polling */}
          {computerStatus.latest && (
            <div
              className="flex shrink-0 items-center gap-2 px-4 py-1.5 text-[12px]"
              style={{
                background: "rgba(0,0,0,0.04)",
                color: "var(--color-cocoa)",
                fontFamily: "var(--font-sans)",
                borderBottom: "1px solid rgba(0,0,0,0.06)",
              }}
              title={describeEvent(computerStatus.latest)}
            >
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: computerStatus.connected ? "#3b9c5c" : "#a05a5a" }}
              />
              <span className="truncate">{describeEvent(computerStatus.latest)}</span>
            </div>
          )}

          {/* messages — only the most recent 7 sealed blocks (cut-bounded) + live tail */}
          <main
            ref={scrollRef}
            className="flex flex-1 flex-col gap-[9px] overflow-y-auto px-4 pt-8 pb-36"
            onScroll={rememberScrollPin}
            onPointerDown={(e) => {
              if (e.target !== e.currentTarget) return
              setAttachOpen(false)
              if (isKeyboardVisibleNow()) blurActiveInput()
            }}
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
                    selectionMode={selectedIds.length > 0}
                    selected={selectedIds.includes(m.id)}
                    onLongSelect={() => startSelecting(m.id)}
                    onSelect={() => toggleSelected(m.id)}
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
                        onPointerDown={onStableControlPointerDown}
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
              ref={composerRef}
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
                    if (stickToBottomRef.current) scrollToBottom("auto")
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
                    onPointerDown={onStableControlPointerDown}
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
                          onPointerDown={(e) => {
                            e.preventDefault()
                            e.stopPropagation()
                            setAttachOpen(false)
                          }}
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
                            onPointerDown={onStableControlPointerDown}
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
                            onPointerDown={onStableControlPointerDown}
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
                  onPointerDown={onStableControlPointerDown}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full"
                  style={{ color: "var(--color-cocoa)" }}
                  aria-label="Voice"
                >
                  <Mic className="h-5 w-5" strokeWidth={1.6} />
                </button>
                <SendButton
                  onStablePointerDown={onStableControlPointerDown}
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
