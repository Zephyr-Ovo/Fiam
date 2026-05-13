import { useCallback, useEffect, useRef, useState } from "react"
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
import { downloadObject, fetchChatTranscript, recordChatMessage, sendChatStream, uploadFiles, recallNow, cutFlow, processFlow, type ChatAttachment, type ChatSegment, type StoredChatMessage } from "./lib/api"
import { useComputerStatus, describeEvent } from "./lib/computerStatus"
import { appConfig } from "./config"
import { createBrowserSttSession, transcribeAudioOpenAICompatible, speakText } from "./lib/voice"

// Module-level set of bubble ids whose entrance animation has already played.
// Skipping replay prevents jank on tab switch / re-render of long histories.
const SEEN_BUBBLE_IDS = new Set<string>()

type Attachment =
  | { kind: "voice"; seconds: number }
  | { kind: "file"; name: string; size?: string | number; object_hash?: string; mime?: string }
  | { kind: "image"; name: string; object_hash?: string; mime?: string; size?: string | number }

type ThinkStep = {
  kind: "think" | "search" | "check" | "native"
  text: string
  summary?: string
  result?: string
  source?: "marker" | "native" | "official" | "fiam"
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

const MAX_RENDERED_MESSAGES = 32

function serverMessagesToMsgs(serverMessages: StoredChatMessage[]): Msg[] {
  const seen = new Set<string>()
  const deduped: StoredChatMessage[] = []
  for (let i = serverMessages.length - 1; i >= 0; i -= 1) {
    const msg = serverMessages[i]
    if (!msg || !msg.id || seen.has(msg.id)) continue
    seen.add(msg.id)
    deduped.unshift(msg)
  }
  return deduped.map((msg) => ({
    ...msg,
    attachments: (msg.attachments || []).map((att) => {
      if (att.kind === "voice") return { kind: "voice", seconds: Number(att.size || 0) || 0 }
      if (att.kind === "image") return { kind: "image", name: att.name, object_hash: att.object_hash, mime: att.mime, size: att.size }
      return { kind: "file", name: att.name, size: att.size, object_hash: att.object_hash, mime: att.mime }
    }),
  }))
}

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
  // Strip directory portion: server sometimes hands us absolute paths
  // ("/home/fiet/.../foo.md") and the long path overflows the pill.
  const displayName = (() => {
    const raw = a.name || ""
    if (!raw) return raw
    const m = raw.match(/[^/\\]+$/)
    return m ? m[0] : raw
  })()
  const bgColor = isImage
    ? "rgba(199,195,176,0.85)" // sage
    : "rgba(255,232,214,0.92)" // peach
  const iconColor = isImage ? "#5a5840" : "var(--color-cocoa)"
  const pillStyle = { background: bgColor, width: 96 }
  const buttonStyle = { ...pillStyle, border: 0, color: "inherit", font: "inherit", cursor: "pointer" }
  const content = (
    <>
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
        {displayName}
      </span>
    </>
  )
  if (a.object_hash) {
    return (
      <button
        type="button"
        className="inline-flex items-center gap-[3px] rounded-[6px] px-[5px] py-[2px] text-left"
        style={buttonStyle}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={() => downloadObject(`obj:${a.object_hash}`, displayName).catch(() => undefined)}
        title="Download attachment"
      >
        {content}
      </button>
    )
  }
  return (
    <div
      className="inline-flex items-center gap-[3px] rounded-[6px] px-[5px] py-[2px]"
      style={pillStyle}
    >
      {content}
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
  // 2026-05-13 batch — chat-state icons (Zephyr/Figma)
  "search",
  "supervise",
  "edit",
  "setting",
  "view",
  "capture",
  "coding",
  "read-book",
  "desktop",
  "sleep",
  "git",
  "game",
  "twitter",
  "video",
  "cat-claw",
  "browser",
  "bulingbuling",
  "butterfly",
  "lock",
  "brain",
])

const EXPLICIT_STREAMLINE_ICON: Record<string, string> = {
  alarmclock: "clock",
  // CC native tools (no exact streamline match → closest visual)
  bash: "coding",
  terminal: "coding",
  shell: "coding",
  read: "file-text",
  glob: "search",
  grep: "search",
  ls: "folder",
  multiedit: "edit",
  write: "write",
  notebookedit: "edit",
  webfetch: "browser",
  websearch: "search",
  task: "clipboard-check",
  todowrite: "clipboard-check",
  filesearch: "search",
  testtube: "clipboard-check",
  zap: "bulingbuling",
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
  edit: "edit",
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
  squarepen: "edit",
  user: "user-profile",
  userround: "user-profile",
  wrench: "setting",
  // 2026-05-13 batch — chat-state icons
  search: "search",
  eye: "view",
  eyeoff: "lock",
  view: "view",
  monitor: "desktop",
  screenshot: "capture",
  camera: "capture",
  code: "coding",
  code2: "coding",
  book: "read-book",
  bookopen: "read-book",
  bookopentext: "read-book",
  desktop: "desktop",
  laptop: "desktop",
  chrome: "desktop",
  moon: "sleep",
  bed: "sleep",
  zzz: "sleep",
  git: "git",
  gitbranch: "git",
  gitcommit: "git",
  github: "git",
  gamepad: "game",
  gamepad2: "game",
  joystick: "game",
  twitter: "twitter",
  x: "twitter",
  video: "video",
  videocamera: "video",
  film: "video",
  playcircle: "video",
  globe: "browser",
  compass: "browser",
  navigation: "browser",
  browser: "browser",
  internet: "browser",
  web: "browser",
  sparkles: "bulingbuling",
  sparkle: "bulingbuling",
  stars: "bulingbuling",
  wand: "bulingbuling",
  wand2: "bulingbuling",
  butterfly: "butterfly",
  catclaw: "cat-claw",
  paw: "cat-claw",
  surfing: "browser",
  surf: "browser",
  lock: "lock",
  locked: "lock",
  shieldcheck: "lock",
  brain: "brain",
  cpu: "brain",
  thinking: "brain",
  nativethinking: "brain",
  thought: "brain",
}

const STREAMLINE_KEYWORD_RULES: Array<{ pattern: RegExp; slug: string }> = [
  { pattern: /\b(read|open|file|document|text|markdown|json|csv|log)\b|文件|文档|日志|读取|查看/, slug: "file-text" },
  { pattern: /\b(list|dir|folder|tree|workspace)\b|目录|文件夹|列表/, slug: "folder" },
  { pattern: /\b(grep|search|find|query|lookup|scan)\b|搜索|检索|查找|寻找/, slug: "search" },
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
  // 1. explicit icon hint from the step -> highest priority.
  const explicit = EXPLICIT_STREAMLINE_ICON[compactIconKey(step.icon)]
  if (explicit && STREAMLINE_THINK_ICONS.has(explicit)) return explicit
  // 1b. pure thinking shows the sparkle icon. Keep this after explicit hints
  // so native/tool-provided icons are not overwritten.
  if (step.kind === "think" && !step.icon && !step.source) return "bulingbuling"
  // 2. Keyword rules use only icon/source labels, never free-form prose;
  //    otherwise words like "file" in normal thinking text produce misleading
  //    file-text icons.
  const haystack = [step.icon, step.source].filter(Boolean).join(" ").toLowerCase()
  if (haystack) {
    for (const rule of STREAMLINE_KEYWORD_RULES) {
      if (rule.pattern.test(haystack) && STREAMLINE_THINK_ICONS.has(rule.slug)) return rule.slug
    }
  }
  // 3. native tool calls without a more specific signal → settings cog
  if (step.source === "native") return "settings"
  // 4. step.kind hints (search/check) when no explicit icon
  if (step.kind === "search") return "search"
  if (step.kind === "check") return "clipboard-check"
  // 5. Unknown non-tool thinking with no other signal -> brain.
  if (!step.icon && !step.source) return "brain"
  return ""
}

function FallbackThinkIcon({ step }: { step: ThinkStep }) {
  const haystack = [step.icon, step.summary, step.text, step.result].filter(Boolean).join(" ").toLowerCase()
  if (step.kind === "search" || /\b(grep|search|find|query|lookup|scan)\b|搜索|检索|查找|寻找/.test(haystack)) return <Search className="h-3.5 w-3.5" strokeWidth={1.6} />
  if (step.kind === "check" || /\b(check|verify|test|build|pass|done)\b|检查|验证|测试|构建|完成/.test(haystack)) return <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.6} />
  return <Brain className="h-3.5 w-3.5" strokeWidth={1.6} />
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
  const fallback = <FallbackThinkIcon step={step} />
  if (!dynamicName) return fallback
  return <DynamicIcon name={dynamicName} className="h-3.5 w-3.5" strokeWidth={1.6} fallback={() => fallback} />
}

function ThinkingChain({ steps, locked, peerName }: { steps: ThinkStep[]; locked?: boolean; peerName?: string }) {
  const [open, setOpen] = useState(false)
  const summary = steps.find((step) => step.summary || step.text)?.summary || steps.find((step) => step.text)?.text
  const summaryStep = steps[0]
  // Detect if this chain is purely thinking vs contains tool actions.
  // Both native reasoning ("think" no source) and <cot> markers
  // (source="marker") count as thinking — they show 'thinking' labels,
  // not 'Used <icon>'.
  const isPureThinking = steps.every((s) => s.kind === "think")
  const sourceLabel = (() => {
    if (!isPureThinking) return ""
    if (summaryStep?.source === "official") return "Native thinking"
    if (summaryStep?.source === "fiam" || summaryStep?.source === "marker") return "Shared thought"
    return ""
  })()
  const toolLabel = (() => {
    if (isPureThinking) return null
    const named = steps.find((s) => s.icon || s.source)
    return (named?.icon || named?.source || "tool").toString()
  })()
  // If the summary looks like a file path, show only the basename so the
  // header doesn't blow out the bubble width.
  const shortSummary = (() => {
    const s = (summary || "").trim()
    if (!s) return s
    if (/^[/\\]|^[A-Za-z]:[/\\]/.test(s) || (s.includes("/") && !s.includes(" "))) {
      const m = s.match(/[^/\\]+$/)
      if (m) return m[0]
    }
    return s
  })()
  // Skip the first step in the expanded list if its text would just repeat
  // the summary line we already show in the header.
  const expandedSteps = steps.filter((s, i) => {
    if (i !== 0) return true
    const t = (s.text || "").trim()
    return !!t && t !== (summary || "").trim()
  })
  if (locked) {
    return (
      <div className="w-full">
        <div
          className="mb-2 inline-flex items-center gap-1.5 text-[12px] leading-[14px]"
          style={{ color: "rgba(63,47,41,0.45)", fontFamily: "var(--font-sans)" }}
        >
          {summaryStep && (
            <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center" style={{ color: "rgba(63,47,41,0.55)" }}>
              <ThinkIcon step={summaryStep} />
            </span>
          )}
          <span className="leading-[14px]">{shortSummary || `${peerName || "AI"} thought silently`}</span>
          <LockIcon className="h-3.5 w-3 shrink-0" strokeWidth={1} />
        </div>
      </div>
    )
  }
  const labeledSummary = sourceLabel && shortSummary && sourceLabel.toLowerCase() !== shortSummary.toLowerCase()
    ? `${sourceLabel}: ${shortSummary}`
    : shortSummary
  const collapsedLabel = labeledSummary || sourceLabel || (isPureThinking ? "Show thinking" : `Used ${toolLabel}`)
  const hasExpandable = expandedSteps.length > 0
  return (
    <div className="w-full">
      {hasExpandable ? (
        <button
          type="button"
          onPointerDown={(e) => {
            e.preventDefault()
            e.stopPropagation()
          }}
          onClick={() => setOpen((v) => !v)}
          className="mb-2 inline-flex items-center gap-1.5 text-[12px] leading-[14px]"
          style={{
            color: "rgba(63,47,41,0.55)",
            fontFamily: "var(--font-sans)",
          }}
        >
          {summaryStep && (
            <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center" style={{ color: "rgba(63,47,41,0.6)" }}>
              <ThinkIcon step={summaryStep} />
            </span>
          )}
          <span className="leading-[14px] text-left">{collapsedLabel}</span>
          <ChevronRight
            className={`h-3 w-3 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
            strokeWidth={2}
          />
        </button>
      ) : (
        <div
          className="mb-2 inline-flex items-center gap-1.5 text-[12px] leading-[14px]"
          style={{
            color: "rgba(63,47,41,0.55)",
            fontFamily: "var(--font-sans)",
          }}
        >
          {summaryStep && (
            <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center" style={{ color: "rgba(63,47,41,0.6)" }}>
              <ThinkIcon step={summaryStep} />
            </span>
          )}
          <span className="leading-[14px] text-left">{collapsedLabel}</span>
        </div>
      )}
      <AnimatePresence initial={false}>
        {open && expandedSteps.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            className="mb-3 overflow-hidden"
          >
            <ol className="flex flex-col gap-1.5">
              {expandedSteps.map((s, i) => {
                const isLast = i === expandedSteps.length - 1
                return (
                  <li key={i} className="grid grid-cols-[6px_1fr] gap-2.5">
                    {/* thin connector rail (no icon — summary header already shows it) */}
                    <div className="relative flex flex-col items-center pt-1.5">
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full"
                        style={{ background: "rgba(63,47,41,0.35)" }}
                      />
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
                        className="md text-[13px] leading-[1.55]"
                        style={{
                          color: "rgba(63,47,41,0.78)",
                          fontFamily: "var(--font-sans)",
                        }}
                      >
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.text}</ReactMarkdown>
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

// Bubble backgrounds are exposed as CSS custom properties so the Settings
// panel can hot-swap them without forcing a re-render of every Bubble.
// Defaults are also baked here as a fallback when the var is missing
// (e.g. during very first paint before config.ts applyThemeVars runs).
const USER_BUBBLE_BG = "var(--user-bubble-bg, rgba(208,188,190,0.92))"
const AGENT_BUBBLE_BG = "var(--agent-bubble-bg, rgba(245,245,245,0.88))"

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

function truncatePreview(value: unknown, max = 90): string {
  if (value == null) return ""
  let s = String(value).replace(/\s+/g, " ").trim()
  if (s.length > max) s = s.slice(0, max - 1) + "…"
  return s
}

// Server emits tool_use then tool_result segments side-by-side. The chat UI
// renders each tool action as its own ThinkingChain with a single step, so
// users see actions one-by-one in the order they happened. Within a tool,
// tool_use + tool_result (matched by tool_use_id) fold into one step.
type RenderItem =
  | { kind: "text"; index: number; text: string }
  | { kind: "thought"; index: number; step: ThinkStep; locked?: boolean }
  | { kind: "tools"; index: number; steps: ThinkStep[] }

function buildRenderItems(segments: ChatSegment[] | undefined): RenderItem[] {
  if (!segments || segments.length === 0) return []
  const out: RenderItem[] = []
  // Track tool steps by id so tool_use + tool_result fold together.
  // Each unique tool_use_id becomes its own RenderItem at the position
  // of its first segment, so consecutive tools render as separate chains.
  const toolMap = new Map<string, { step: ThinkStep; index: number }>()
  segments.forEach((seg, idx) => {
    if (seg.type === "text") {
      if (seg.text) out.push({ kind: "text", index: idx, text: seg.text })
    } else if (seg.type === "thought") {
      if (seg.text || seg.summary) {
        out.push({ kind: "thought", index: idx, step: stepFromSegment(seg), locked: seg.locked })
      }
    } else if (seg.type === "tool_use") {
      const id = seg.tool_use_id || `anon-${idx}`
      const existing = toolMap.get(id)
      if (!existing) {
        toolMap.set(id, {
          index: idx,
          step: {
            kind: "native",
            source: "native",
            icon: seg.tool_name,
            text: truncatePreview(seg.input_summary),
          },
        })
      } else {
        if (seg.tool_name) existing.step.icon = seg.tool_name
        if (seg.input_summary) existing.step.text = truncatePreview(seg.input_summary)
      }
    } else if (seg.type === "tool_result") {
      const id = seg.tool_use_id || `anon-${idx}`
      const existing = toolMap.get(id)
      if (existing) {
        if (seg.tool_name && !existing.step.icon) existing.step.icon = seg.tool_name
        existing.step.result = truncatePreview(seg.result_summary, 140)
      } else {
        toolMap.set(id, {
          index: idx,
          step: {
            kind: "native",
            source: "native",
            icon: seg.tool_name,
            text: "",
            result: truncatePreview(seg.result_summary, 140),
          },
        })
      }
    }
  })
  toolMap.forEach((entry) => {
    if (entry.step.text || entry.step.result) {
      out.push({ kind: "tools", index: entry.index, steps: [entry.step] })
    }
  })
  out.sort((a, b) => a.index - b.index)
  return out
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
  const orderedSegments: RenderItem[] = !isUser ? buildRenderItems(msg.segments) : []
  // Only animate the FIRST time we see this message id; on re-render skip
  // entrance entirely (no jank scrolling/switching tabs back to chat).
  const wasSeen = SEEN_BUBBLE_IDS.has(msg.id)
  if (!wasSeen) SEEN_BUBBLE_IDS.add(msg.id)

  // Build "blocks" — when actions interrupt a reply, each block renders as
  // its own bubble row. A block is either: a run of consecutive text items,
  // OR a single chain (thought/tools). Voice attachments lead, file
  // attachments trail. Only the first visible block carries the name tag.
  type Block =
    | { kind: "text-group"; items: Extract<RenderItem, { kind: "text" }>[]; firstIndex: number }
    | { kind: "chain-thought"; item: Extract<RenderItem, { kind: "thought" }> }
    | { kind: "chain-tools"; item: Extract<RenderItem, { kind: "tools" }> }
    | { kind: "voice" }
    | { kind: "files" }
  const blocks: Block[] = []
  if (voiceAttachments.length > 0) blocks.push({ kind: "voice" })
  if (orderedSegments.length > 0) {
    let buf: Extract<RenderItem, { kind: "text" }>[] = []
    let bufIdx = -1
    const flush = () => {
      if (buf.length > 0) {
        blocks.push({ kind: "text-group", items: buf, firstIndex: bufIdx })
        buf = []
        bufIdx = -1
      }
    }
    for (const item of orderedSegments) {
      if (item.kind === "text") {
        if (buf.length === 0) bufIdx = item.index
        buf.push(item)
      } else if (item.kind === "thought") {
        flush()
        blocks.push({ kind: "chain-thought", item })
      } else {
        flush()
        blocks.push({ kind: "chain-tools", item })
      }
    }
    flush()
  } else if (msg.text) {
    // Synthetic single text block from msg.text fallback
    blocks.push({
      kind: "text-group",
      items: [{ kind: "text", index: 0, text: msg.text }],
      firstIndex: 0,
    })
  }
  const hasAgentLockedThoughts = !isUser && orderedSegments.length === 0 && (msg.thinkingLocked || (msg.thinking && msg.thinking.length > 0))
  if (hasAgentLockedThoughts) {
    // Promote msg.thinking into a single chain-thought-equivalent block at start
    blocks.unshift({
      kind: "chain-tools",
      item: { kind: "tools", index: -1, steps: msg.thinking || [] },
    })
  }
  if (fileAttachments.length > 0) blocks.push({ kind: "files" })
  if (blocks.length === 0) {
    // Empty message — nothing to render
    return null
  }
  // Determine which block index gets the NameTag (first visible one)
  const nameBlockIdx = 0

  const renderBlock = (block: Block, blockIdx: number) => {
    const isFirstBlock = blockIdx === nameBlockIdx
    const showThisName = showName && isFirstBlock
    return (
      <motion.div
        key={`${msg.id}-block-${blockIdx}`}
        initial={wasSeen ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: "easeOut", delay: wasSeen ? 0 : Math.min(blockIdx * 0.04, 0.2) }}
        className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}
      >
        <div
          className={`flex max-w-[82%] min-w-0 flex-col gap-1 ${
            isUser ? "items-end" : "items-start"
          }`}
        >
          {showThisName && (
            <NameTag>{isUser ? (appConfig.userName || "you") : peerName}</NameTag>
          )}
          {block.kind === "voice" && (
            <Attachments list={voiceAttachments} isUser={isUser} />
          )}
          {block.kind === "files" && (
            <Attachments list={fileAttachments} isUser={isUser} />
          )}
          {block.kind === "chain-thought" && (
            <ThinkingChain
              steps={[block.item.step]}
              locked={msg.thinkingLocked || !!block.item.locked}
              peerName={peerName}
            />
          )}
          {block.kind === "chain-tools" && (
            <ThinkingChain
              steps={block.item.steps}
              locked={msg.thinkingLocked}
              peerName={peerName}
            />
          )}
          {block.kind === "text-group" && (
            <BubbleBody
              key={`${msg.id}-seg-${block.firstIndex}`}
              text={block.items.map((item) => item.text).join("")}
              isUser={isUser}
              recallUsed={!!msg.recallUsed && blockIdx === 0}
              selectionMode={!!selectionMode}
              selected={!!selected}
              onSelect={onSelect}
              onLongSelect={onLongSelect}
            />
          )}
        </div>
      </motion.div>
    )
  }

  return <>{blocks.map(renderBlock)}</>
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
    <div ref={wrapRef} className="relative inline-block max-w-full" style={{ overflow: "visible" }}>
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
        className={`md relative max-w-full px-4 py-3 text-[14.5px] leading-[1.6] ${
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
      aria-label="Send message"
      className="grid h-9 w-9 place-items-center rounded-full transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
      style={{ color: "var(--color-cocoa)" }}
    >
      <Send className="h-5 w-5" strokeWidth={1.8} />
    </button>
  )
}

export default function App({ onBack, active = true }: { onBack?: () => void; active?: boolean } = {}) {
  const peerName = appConfig.aiName || "ai"
  const computerStatus = useComputerStatus(active)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [sharing, setSharing] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<{ id: string; file: File }[]>([])
  type PendingChatTurn = {
    text: string
    filesToSend: File[]
    recallUsed: boolean
  }
  const scrollRef = useRef<HTMLElement | null>(null)
  const composerRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const cameraInputRef = useRef<HTMLInputElement | null>(null)
  const [attachOpen, setAttachOpen] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const [voiceRecording, setVoiceRecording] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const browserSttRef = useRef<ReturnType<typeof createBrowserSttSession> | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const mediaChunksRef = useRef<Blob[]>([])
  const confirmTimerRef = useRef<number | null>(null)
  const maxViewportHeightRef = useRef(0)
  const stickToBottomRef = useRef(true)
  // Mirror `sending` into a ref so background refetches (visibilitychange,
  // config-changed) can bail out without racing an in-flight stream.
  const sendingRef = useRef(false)
  useEffect(() => { sendingRef.current = sending }, [sending])

  const mergeServerMessages = useCallback((serverMessages: StoredChatMessage[], opts: { dropLocalId?: string; dropLocalUserText?: string } = {}) => {
    const fromServer = serverMessagesToMsgs(serverMessages)
    setMessages((local) => {
      const serverIds = new Set(fromServer.map((m) => m.id))
      const dropUserText = (opts.dropLocalUserText || "").trim()
      const localOnly = local.filter((m) => (
        m.id !== opts.dropLocalId &&
        !serverIds.has(m.id) &&
        !(dropUserText && m.role === "user" && String(m.text || "").trim() === dropUserText)
      ))
      return [...fromServer, ...localOnly]
    })
    return fromServer
  }, [])

  async function recoverTranscriptAfterStreamError(localAiId: string, sinceMinute: number, expectedUserText: string) {
    const res = await fetchChatTranscript("chat")
    if (!res.ok || !res.messages || res.messages.length === 0) return false
    const expected = expectedUserText.trim()
    let userIdx = -1
    if (expected) {
      for (let i = res.messages.length - 1; i >= 0; i -= 1) {
        const m = res.messages[i]
        const body = String(m.raw_text || m.text || "").trim()
        if (m.role === "user" && body === expected) {
          userIdx = i
          break
        }
      }
    }
    const recovered = res.messages.some((m, i) => (
      m.role === "ai" &&
      !m.error &&
      (userIdx >= 0 ? i > userIdx : (typeof m.t === "number" && m.t >= sinceMinute - 1))
    ))
    mergeServerMessages(res.messages, {
      ...(recovered ? { dropLocalId: localAiId } : {}),
      ...(userIdx >= 0 ? { dropLocalUserText: expected } : {}),
    })
    return recovered
  }

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
    } finally {
      setSharing(false)
    }
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
    if (!active) return
    let cancelled = false
    function loadTranscript() {
      // Skip while a stream is in flight — replacing messages mid-stream
      // wipes the in-progress AI bubble and the streamed segments never
      // surface (root cause of "回复消失，去网页才看到").
      if (sendingRef.current) return
      fetchChatTranscript("chat")
        .then((res) => {
          if (cancelled || !res.ok || !res.messages || res.messages.length === 0) return
          mergeServerMessages(res.messages)
        })
        .catch(() => {})
    }
    loadTranscript()
    // Settings page may set the auth token AFTER mount. Refetch when the
    // saved config changes so the user sees their history without restarting
    // the app. Also refetch when the tab/page regains visibility (e.g. after
    // unlocking the phone or coming back from another app), in case the
    // server got new turns from CC while we were away.
    function onConfigChanged() { loadTranscript() }
    function onVisibility() { if (document.visibilityState === "visible") loadTranscript() }
    window.addEventListener("favilla:config-changed", onConfigChanged)
    document.addEventListener("visibilitychange", onVisibility)
    return () => {
      cancelled = true
      window.removeEventListener("favilla:config-changed", onConfigChanged)
      document.removeEventListener("visibilitychange", onVisibility)
    }
  }, [active, mergeServerMessages])

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
      if (confirmTimerRef.current !== null) window.clearTimeout(confirmTimerRef.current)
      browserSttRef.current?.stop()
      mediaRecorderRef.current?.stop()
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
    }
  }, [])

  function appendVoiceText(text: string) {
    const clean = text.trim()
    if (!clean) return
    setInput((cur) => (cur.trim() ? `${cur.trimEnd()} ${clean}` : clean))
  }

  async function startApiSttRecording() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    mediaStreamRef.current = stream
    mediaChunksRef.current = []
    const recorder = new MediaRecorder(stream)
    mediaRecorderRef.current = recorder
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) mediaChunksRef.current.push(event.data)
    }
    recorder.onstop = () => {
      setVoiceRecording(false)
      const blob = new Blob(mediaChunksRef.current, { type: mediaChunksRef.current[0]?.type || "audio/webm" })
      mediaChunksRef.current = []
      mediaRecorderRef.current = null
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
      mediaStreamRef.current = null

      void (async () => {
        if (blob.size <= 0) return
        try {
          const text = await transcribeAudioOpenAICompatible(blob)
          appendVoiceText(text)
        } catch (error) {
          setVoiceError(error instanceof Error ? error.message : String(error))
        } finally {
          setVoiceBusy(false)
        }
      })()
    }
    recorder.start()
    setVoiceError(null)
    setVoiceRecording(true)
    setVoiceBusy(true)
  }

  async function toggleVoiceInput() {
    if (voiceRecording) {
      if (appConfig.sttProvider === "browser") {
        browserSttRef.current?.stop()
      } else {
        mediaRecorderRef.current?.stop()
      }
      return
    }

    try {
      setVoiceError(null)
      if (appConfig.sttProvider === "openai_compatible") {
        await startApiSttRecording()
        return
      }

      const session = createBrowserSttSession({
        onFinalText: appendVoiceText,
        onError: (message) => {
          setVoiceError(message)
          setVoiceBusy(false)
          setVoiceRecording(false)
        },
        onEnd: () => {
          setVoiceBusy(false)
          setVoiceRecording(false)
        },
      })
      if (!session) {
        setVoiceError("speech recognition is not available")
        return
      }

      browserSttRef.current = session
      setVoiceBusy(true)
      setVoiceRecording(true)
      session.start()
    } catch (error) {
      setVoiceBusy(false)
      setVoiceRecording(false)
      setVoiceError(error instanceof Error ? error.message : String(error))
    }
  }

  async function sendChatTurns(items: PendingChatTurn[]) {
    if (!items.length) return

    setSending(true)
    stickToBottomRef.current = true
    const sendStartedMinute = currentT()
    const aiId = `a-${Date.now()}`
    let expectedStreamText = ""
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
      expectedStreamText = combinedText || "(see attached file)"
      const segs: ChatSegment[] = []
      const thoughts: ThinkStep[] = []
      const thoughtSegIdx = new Map<number, number>()
      let lastError: string | null = null

      const apply = (next: { segments?: ChatSegment[]; thoughts?: ThinkStep[]; thoughtsLocked?: boolean; text?: string; hold?: Msg["hold"]; attachments?: Attachment[] }) => {
        setMessages((m) =>
          m.map((x) =>
            x.id === aiId
              ? {
                  ...x,
                  ...(next.text !== undefined ? { text: next.text } : {}),
                  ...(next.segments !== undefined ? { segments: next.segments } : {}),
                  ...(next.thoughts !== undefined ? { thinking: next.thoughts.length > 0 ? next.thoughts : undefined } : {}),
                  ...(next.thoughtsLocked !== undefined ? { thinkingLocked: next.thoughtsLocked } : {}),
                  ...(next.hold !== undefined ? { hold: next.hold } : {}),
                  ...(next.attachments !== undefined ? { attachments: next.attachments } : {}),
                }
              : x,
          ),
        )
      }

      await sendChatStream(expectedStreamText, "chat", attachments, appConfig.defaultRuntime, (ev) => {
        if (ev.event === "tool_use") {
          segs.push({
            type: "tool_use",
            tool_use_id: ev.data.tool_use_id,
            tool_name: ev.data.tool_name,
            input_summary: ev.data.input_summary,
          })
          apply({ segments: [...segs] })
        } else if (ev.event === "tool_result") {
          segs.push({
            type: "tool_result",
            tool_use_id: ev.data.tool_use_id,
            tool_name: ev.data.tool_name,
            result_summary: ev.data.result_summary,
            is_error: ev.data.is_error,
          })
          apply({ segments: [...segs] })
        } else if (ev.event === "thought") {
          const segIdx = segs.length
          segs.push({
            type: "thought",
            kind: "think",
            text: ev.data.text,
            summary: ev.data.summary,
            icon: ev.data.icon,
            source: ev.data.source,
            locked: ev.data.locked,
          })
          thoughtSegIdx.set(ev.data.index, segIdx)
          thoughts.push({
            kind: "think",
            text: ev.data.text,
            summary: ev.data.summary,
            icon: ev.data.icon,
            source: ev.data.source,
            locked: ev.data.locked,
          })
          apply({ segments: [...segs], thoughts: [...thoughts] })
        } else if (ev.event === "thought_summary") {
          const segIdx = thoughtSegIdx.get(ev.data.index)
          if (segIdx !== undefined && segs[segIdx] && segs[segIdx].type === "thought") {
            segs[segIdx] = { ...segs[segIdx], summary: ev.data.summary, icon: ev.data.icon } as ChatSegment
            if (thoughts[ev.data.index]) {
              thoughts[ev.data.index] = { ...thoughts[ev.data.index], summary: ev.data.summary || thoughts[ev.data.index].summary, icon: ev.data.icon || thoughts[ev.data.index].icon }
            }
            apply({ segments: [...segs], thoughts: [...thoughts] })
          }
        } else if (ev.event === "text_delta") {
          segs.push({ type: "text", text: ev.data.text })
          apply({ segments: [...segs] })
        } else if (ev.event === "done") {
          const r = ev.data
          // Final reconciliation: trust server's authoritative segments/thoughts/hold/text.
          const finalThoughts: ThinkStep[] = (r.thoughts || []).map((t) => ({
            kind: t.kind || "think",
            text: t.text,
            summary: t.summary,
            result: t.result,
            source: t.source,
            locked: t.locked,
            icon: t.icon,
          }))
          apply({
            text: r.reply || "",
            segments: r.segments,
            thoughts: finalThoughts,
            thoughtsLocked: !!r.thoughts_locked,
            hold: r.hold,
            attachments: (r.attachments || []).map((att) => {
              if (att.kind === "voice") return { kind: "voice", seconds: Number(att.size || 0) || 0 }
              if (att.kind === "image") return { kind: "image", name: att.name, object_hash: att.object_hash, mime: att.mime, size: att.size }
              return { kind: "file", name: att.name, size: att.size, object_hash: att.object_hash, mime: att.mime }
            }),
          })
          try {
            window.dispatchEvent(
              new CustomEvent("favilla:newAiReply", {
                detail: { peerName, preview: r.reply || "" },
              }),
            )
          } catch { /* ignore */ }
          if (appConfig.ttsAutoPlayAi && String(r.reply || "").trim()) {
            void speakText(String(r.reply || "")).catch((error) => {
              setVoiceError(error instanceof Error ? error.message : String(error))
            })
          }
        } else if (ev.event === "error") {
          lastError = ev.data.message || "unknown"
        }
      })

      if (lastError) {
        const recovered = await recoverTranscriptAfterStreamError(aiId, sendStartedMinute, expectedStreamText).catch(() => false)
        if (recovered) return
        setMessages((m) =>
          m.map((x) =>
            x.id === aiId
              ? { ...x, text: `error: ${lastError}`, error: true, segments: undefined, thinking: undefined }
              : x,
          ),
        )
        return
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      const recovered = await recoverTranscriptAfterStreamError(aiId, sendStartedMinute, expectedStreamText).catch(() => false)
      if (recovered) return
      setMessages((m) =>
        m.map((x) =>
          x.id === aiId
            ? { ...x, text: `network error: ${msg}`, error: true, segments: undefined, thinking: undefined }
            : x,
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
            ? ({ kind: "image", name: file.name, object_hash: file.object_hash, mime: file.mime, size: file.size } as const)
            : ({ kind: "file", name: file.name, size: file.size, object_hash: file.object_hash, mime: file.mime } as const)
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
          object_hash: file.object_hash,
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

    void sendChatTurns([{ text, filesToSend, recallUsed: wasArmed }])
  }

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey) return
    e.preventDefault()
    if (input.trim() || pendingFiles.length > 0) {
      handleSend()
    }
  }

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden">
      <div
        className="absolute inset-0 -z-10 bg-cover bg-center"
        style={{ backgroundImage: `url(${appConfig.bg})` }}
      />

      <div className="relative flex h-full min-h-0 flex-col">
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
            className="chat-scroll flex min-h-0 flex-1 flex-col gap-[9px] overflow-y-auto overflow-x-hidden px-4 pt-8 pb-36"
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
              return messages.slice(startIdx).slice(-MAX_RENDERED_MESSAGES)
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
                  onClick={() => {
                    void toggleVoiceInput()
                  }}
                  disabled={voiceBusy && !voiceRecording}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full"
                  style={{
                    color: "var(--color-cocoa)",
                    background: voiceRecording ? "rgba(176, 76, 76, 0.14)" : "transparent",
                    opacity: voiceBusy && !voiceRecording ? 0.55 : 1,
                  }}
                  aria-label={voiceRecording ? "Stop voice input" : "Voice input"}
                  aria-busy={voiceBusy && !voiceRecording}
                  title={voiceRecording ? "Tap to stop recording" : "Tap to start speech-to-text"}
                >
                  <Mic className="h-5 w-5" strokeWidth={1.6} />
                </button>
                <SendButton
                  onStablePointerDown={onStableControlPointerDown}
                  onSend={() => {
                    if (input.trim() || pendingFiles.length > 0) handleSend()
                  }}
                  disabled={sealBusy || sending || (!input.trim() && pendingFiles.length === 0)}
                />
              </div>
              {voiceError && (
                <div
                  className="px-2 pb-1 text-[11px]"
                  style={{ color: "#A74A3A", fontFamily: "var(--font-sans)" }}
                >
                  voice: {voiceError}
                </div>
              )}
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
