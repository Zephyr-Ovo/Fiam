import { useEffect, useState } from "react"
import {
  appConfig, saveConfig, type AppConfig,
  BG_IDB, AVATAR_USER_IDB, AVATAR_AI_IDB, AVATAR_USER_KEY, AVATAR_AI_KEY,
} from "../config"
import { saveImage, loadImage, clearImage } from "../lib/bg-store"
import { pickImageDataUrl } from "../lib/pick-image"

type Props = {
  open: boolean
  onClose: () => void
}

/**
 * Settings — frosted-glass popover. Visual: warm cream blur over the dim
 * scene. Single-layer composite — the entire popup animates as one
 * transform+opacity layer. Shell hides Home while open so backdrop-filter
 * only has to blur a near-empty layer (this fixes the "paints in waves" lag
 * the user reported).
 *
 * Layout: minimal — no boxed inputs, just labels and bottom-line fields.
 */
export function Settings({ open, onClose }: Props) {
  const [draft, setDraft] = useState<AppConfig>(appConfig)
  const [mounted, setMounted] = useState(open)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (open) {
      setDraft({ ...appConfig })
      setMounted(true)
      const id = window.requestAnimationFrame(() => setVisible(true))
      return () => window.cancelAnimationFrame(id)
    } else if (mounted) {
      setVisible(false)
      const id = window.setTimeout(() => setMounted(false), 200)
      return () => window.clearTimeout(id)
    }
  }, [open, mounted])

  if (!mounted) return null

  function commit() {
    saveConfig(draft)
    onClose()
  }

  return (
    <div
      className="absolute inset-0 z-40"
      style={{ pointerEvents: visible ? "auto" : "none" }}
    >
      <button
        type="button"
        aria-label="Dismiss settings"
        onClick={onClose}
        className="absolute inset-0"
        style={{
          background: "rgba(0,0,0,0.45)",
          border: 0,
          padding: 0,
          cursor: "default",
          opacity: visible ? 1 : 0,
          transition: "opacity 120ms ease-out",
        }}
      />
      <div
        role="dialog"
        aria-label="Settings"
        className="absolute left-1/2 top-1/2 flex flex-col"
        style={{
          transform: visible
            ? "translate(-50%, -50%) scale(1)"
            : "translate(-50%, -48%) scale(0.96)",
          width: "min(340px, calc(100% - 32px))",
          maxWidth: 340,
          height: "min(430px, calc(100dvh - 88px))",
          maxHeight: "calc(100dvh - 88px)",
          borderRadius: 20,
          background: "rgba(255, 250, 243, 0.55)",
          backdropFilter: "blur(20px) saturate(150%)",
          WebkitBackdropFilter: "blur(20px) saturate(150%)",
          border: "1px solid rgba(255, 255, 255, 0.6)",
          boxShadow:
            "0 18px 50px -12px rgba(40, 28, 22, 0.45), 0 1px 0 rgba(255,255,255,0.7) inset",
          padding: "16px 20px 14px",
          color: "#3f2f29",
          fontFamily: "var(--font-sans)",
          opacity: visible ? 1 : 0,
          transition:
            "opacity 120ms ease-out, transform 120ms ease-out",
          willChange: "transform, opacity",
          overflow: "hidden",
        }}
      >
        <div
          className="mb-0.5 text-center text-[15px] font-medium tracking-wide"
          style={{ color: "#3f2f29" }}
        >
          Settings
        </div>
        <div
          className="mb-2.5 text-center text-[10px]"
          style={{ color: "rgba(63,47,41,0.4)", fontFamily: "var(--font-mono, var(--font-sans))" }}
        >
          build {__BUILD_ID__}
        </div>

        <div
          className="settings-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain"
          style={{ WebkitOverflowScrolling: "touch" }}
        >

        <Field
          label="Your name"
          value={draft.userName}
          onChange={(v) => setDraft({ ...draft, userName: v })}
        />
        <Field
          label="AI name"
          value={draft.aiName}
          onChange={(v) => setDraft({ ...draft, aiName: v })}
          placeholder="ai"
        />
        <Field
          label="API base"
          value={draft.apiBase}
          onChange={(v) => setDraft({ ...draft, apiBase: v })}
          placeholder="https://fiet.cc"
        />
        <Field
          label="Ingest token"
          value={draft.ingestToken}
          onChange={(v) => setDraft({ ...draft, ingestToken: v })}
          placeholder="X-Fiam-Token"
          secret
        />
        <Field
          label="Limen URL"
          value={draft.limenBaseUrl}
          onChange={(v) => setDraft({ ...draft, limenBaseUrl: v })}
          placeholder="http://192.168.39.19"
        />
        <RuntimeField
          value={draft.defaultRuntime}
          onChange={(v) => {
            setDraft((cur) => ({ ...cur, defaultRuntime: v }))
            saveConfig({ defaultRuntime: v })
          }}
        />
        <BoolField
          label="Auto speak AI"
          value={draft.ttsAutoPlayAi}
          onChange={(v) => setDraft({ ...draft, ttsAutoPlayAi: v })}
        />
        <ColorRow
          items={[
            {
              label: "你",
              value: draft.userBubbleBg,
              onChange: (v) => setDraft({ ...draft, userBubbleBg: v }),
            },
            {
              label: "AI",
              value: draft.agentBubbleBg,
              onChange: (v) => setDraft({ ...draft, agentBubbleBg: v }),
            },
            {
              label: "主题",
              value: draft.themeColor,
              onChange: (v) => setDraft({ ...draft, themeColor: v }),
            },
          ]}
        />
        <ImagePickField
          label="Background"
          value={draft.bg}
          sentinel={BG_IDB}
          idbKey="bg"
          maxDim={1400}
          onChange={(v) => {
            setDraft((cur) => ({ ...cur, bg: v }))
            saveConfig({ bg: v })
          }}
        />
        <ImagePickField
          label="Your avatar"
          value={draft.userAvatar}
          sentinel={AVATAR_USER_IDB}
          idbKey={AVATAR_USER_KEY}
          round
          maxDim={256}
          onChange={(v) => {
            setDraft((cur) => ({ ...cur, userAvatar: v }))
            saveConfig({ userAvatar: v })
          }}
        />
        <ImagePickField
          label="AI avatar"
          value={draft.aiAvatar}
          sentinel={AVATAR_AI_IDB}
          idbKey={AVATAR_AI_KEY}
          round
          maxDim={256}
          onChange={(v) => {
            setDraft((cur) => ({ ...cur, aiAvatar: v }))
            saveConfig({ aiAvatar: v })
          }}
        />
        </div>

        <div className="mt-4 flex shrink-0 justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-4 py-1.5 text-[13px]"
            style={{
              color: "rgba(63, 47, 41, 0.7)",
              background: "rgba(63, 47, 41, 0.08)",
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={commit}
            className="rounded-full px-5 py-1.5 text-[13px] font-medium"
            style={{
              color: "#fff",
              background: "var(--color-cocoa)",
              boxShadow: "0 1px 0 rgba(255, 255, 255, 0.18) inset",
            }}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  )
}

function RuntimeField({
  value,
  onChange,
}: {
  value: AppConfig["defaultRuntime"]
  onChange: (v: AppConfig["defaultRuntime"]) => void
}) {
  return (
    <div className="block" style={{ paddingTop: 8, paddingBottom: 8 }}>
      <div
        className="text-[10.5px] uppercase tracking-[0.08em]"
        style={{ color: "rgba(63, 47, 41, 0.55)" }}
      >
        Runtime
      </div>
      <div
        className="mt-1 grid grid-cols-3 gap-1 rounded-full p-1"
        style={{ background: "rgba(63, 47, 41, 0.09)" }}
      >
        {(["auto", "api", "cc"] as const).map((runtime) => {
          const active = value === runtime
          return (
            <button
              key={runtime}
              type="button"
              onClick={() => onChange(runtime)}
              className="rounded-full px-3 py-1.5 text-[13px] font-medium transition-colors"
              style={{
                color: active ? "#fff" : "rgba(63, 47, 41, 0.68)",
                background: active ? "var(--color-cocoa)" : "transparent",
              }}
            >
              {runtime === "auto" ? "AI" : runtime.toUpperCase()}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  secret,
  last,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  secret?: boolean
  last?: boolean
}) {
  const [focused, setFocused] = useState(false)
  return (
    <label
      className="block"
      style={{
        paddingTop: 8,
        paddingBottom: 8,
        borderBottom: last ? "none" : "1px solid rgba(63, 47, 41, 0.12)",
      }}
    >
      <div
        className="text-[10.5px] uppercase tracking-[0.08em]"
        style={{ color: "rgba(63, 47, 41, 0.55)" }}
      >
        {label}
      </div>
      <input
        type={secret ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={placeholder}
        spellCheck={false}
        className="w-full bg-transparent px-0 py-1 text-[14px] outline-none"
        style={{
          color: "#3f2f29",
          fontFamily: "var(--font-sans)",
          boxShadow: focused
            ? "inset 0 -1px 0 rgba(176, 139, 127, 0.85)"
            : "inset 0 -1px 0 rgba(63, 47, 41, 0.05)",
          transition: "box-shadow 140ms ease-out",
        }}
      />
    </label>
  )
}

// ColorRow — three compact hex swatches in one row. Each item shows a
// small swatch driven by the native color picker plus a short hex code
// underneath. Saves vertical space and lets users compare the three
// theme colors side-by-side. Non-hex existing values are coerced into
// hex via toHex on first interaction.
function ColorSwatch({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const hex = toHex(value) || "#cccccc"
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(hex)

  const commitHex = () => {
    setEditing(false)
    const clean = draft.trim()
    if (/^#[0-9a-fA-F]{6}$/.test(clean)) {
      onChange(clean)
    } else if (/^[0-9a-fA-F]{6}$/.test(clean)) {
      onChange(`#${clean}`)
    }
  }

  return (
    <div className="flex flex-col items-center gap-1">
      <span
        aria-hidden
        style={{
          display: "inline-block", width: 32, height: 32, borderRadius: 8,
          background: hex, border: "1px solid rgba(63,47,41,0.18)",
          position: "relative", overflow: "hidden",
        }}
      >
        <input
          type="color"
          value={hex}
          onChange={(e) => onChange(e.target.value)}
          aria-label={`${label} color`}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", opacity: 0, cursor: "pointer", border: 0, padding: 0 }}
        />
      </span>
      <span className="text-[10px] tracking-tight" style={{ color: "rgba(63,47,41,0.7)", fontFamily: "var(--font-mono, var(--font-sans))" }}>
        {label}
      </span>
      {editing ? (
        <input
          autoFocus
          className="text-[10px] w-16 text-center rounded border outline-none"
          style={{ color: "rgba(63,47,41,0.8)", fontFamily: "var(--font-mono)", borderColor: "rgba(63,47,41,0.2)", padding: "1px 2px" }}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitHex}
          onKeyDown={(e) => { if (e.key === "Enter") commitHex() }}
        />
      ) : (
        <span
          className="text-[10px] cursor-pointer"
          style={{ color: "rgba(63,47,41,0.5)", fontFamily: "var(--font-mono)" }}
          onClick={() => { setDraft(hex); setEditing(true) }}
        >
          {hex}
        </span>
      )}
    </div>
  )
}

function ColorRow({
  items,
}: {
  items: { label: string; value: string; onChange: (v: string) => void }[]
}) {
  return (
    <div
      style={{ paddingTop: 10, paddingBottom: 10, borderBottom: "1px solid rgba(63, 47, 41, 0.12)" }}
    >
      <div className="text-[10.5px] uppercase tracking-[0.08em]" style={{ color: "rgba(63, 47, 41, 0.55)" }}>
        Colors
      </div>
      <div className="mt-2 flex items-center gap-4">
        {items.map((it) => (
          <ColorSwatch key={it.label} label={it.label} value={it.value} onChange={it.onChange} />
        ))}
      </div>
    </div>
  )
}

// Best-effort coercion of any CSS color string into #RRGGBB. Returns "" if
// it can't be resolved (e.g. SSR / unknown name). Uses a hidden canvas as
// the parser, which handles named colors, hex, rgb/rgba, hsl, etc.
function toHex(input: string): string {
  if (!input) return ""
  if (/^#([0-9a-f]{6})$/i.test(input)) return input.toLowerCase()
  if (typeof document === "undefined") return ""
  try {
    const ctx = document.createElement("canvas").getContext("2d")
    if (!ctx) return ""
    ctx.fillStyle = "#000"
    ctx.fillStyle = input
    const out = ctx.fillStyle
    if (typeof out === "string" && /^#[0-9a-f]{6}$/i.test(out)) return out.toLowerCase()
    return ""
  } catch {
    return ""
  }
}

// ImagePickField — generic image picker (background + avatars). Uses the
// native OS photo picker on device (Capacitor Camera) with a web <input>
// fallback. The picked image is downscaled and stored in IndexedDB; only a
// tiny sentinel goes into the localStorage config blob.
function ImagePickField({
  label,
  value,
  sentinel,
  idbKey,
  round,
  maxDim,
  onChange,
}: {
  label: string
  value: string
  sentinel: string
  idbKey: string
  round?: boolean
  maxDim?: number
  onChange: (v: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [preview, setPreview] = useState(value === sentinel ? "" : value)

  useEffect(() => {
    let alive = true
    if (value === sentinel) {
      loadImage(idbKey).then((d) => { if (alive) setPreview(d || "") })
    } else {
      setPreview(value)
    }
    return () => { alive = false }
  }, [value, sentinel, idbKey])

  const pick = async () => {
    setError("")
    setBusy(true)
    try {
      const dataUrl = await pickImageDataUrl(maxDim ?? 1200)
      if (!dataUrl) { setBusy(false); return }
      await saveImage(idbKey, dataUrl)
      setPreview(dataUrl)
      onChange(sentinel)
    } catch {
      setError("Couldn't load image")
    } finally {
      setBusy(false)
    }
  }

  const side = round ? 44 : 56
  return (
    <div style={{ paddingTop: 10, paddingBottom: 10 }}>
      <div
        className="text-[10.5px] uppercase tracking-[0.08em]"
        style={{ color: "rgba(63, 47, 41, 0.55)" }}
      >
        {label}
      </div>
      <div className="mt-2 flex items-center gap-3">
        <span
          aria-hidden
          style={{
            display: "inline-block",
            width: side,
            height: side,
            borderRadius: round ? "50%" : 8,
            backgroundImage: preview ? `url(${preview})` : "none",
            backgroundColor: preview ? "transparent" : "rgba(63,47,41,0.08)",
            backgroundSize: "cover",
            backgroundPosition: "center",
            border: "1px solid rgba(63,47,41,0.18)",
          }}
        />
        <button
          type="button"
          onClick={pick}
          disabled={busy}
          className="rounded-full px-3 py-1 text-[12px]"
          style={{
            color: "rgba(63, 47, 41, 0.85)",
            background: "rgba(63, 47, 41, 0.08)",
            opacity: busy ? 0.6 : 1,
          }}
        >
          {busy ? "Loading…" : "Choose"}
        </button>
        {preview && (
          <button
            type="button"
            onClick={() => { clearImage(idbKey); setPreview(""); onChange("") }}
            className="rounded-full px-3 py-1 text-[12px]"
            style={{ color: "rgba(63, 47, 41, 0.6)", background: "transparent" }}
          >
            Reset
          </button>
        )}
      </div>
      {error && (
        <div className="mt-1 text-[11px]" style={{ color: "#a83a2a" }}>
          {error}
        </div>
      )}
    </div>
  )
}

function BoolField({
  label,
  value,
  onChange,
}: {
  label: string
  value: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label
      className="flex items-center justify-between cursor-pointer"
      style={{
        paddingTop: 8,
        paddingBottom: 8,
        borderBottom: "1px solid rgba(63, 47, 41, 0.12)",
      }}
    >
      <span
        className="text-[10.5px] uppercase tracking-[0.08em]"
        style={{ color: "rgba(63, 47, 41, 0.55)" }}
      >
        {label}
      </span>
      <div
        className="relative rounded-full transition-colors"
        style={{
          width: 40,
          height: 22,
          background: value ? "var(--color-cocoa)" : "rgba(63, 47, 41, 0.2)",
        }}
        onClick={() => onChange(!value)}
      >
        <div
          className="absolute top-[2px] rounded-full bg-white shadow-sm transition-transform"
          style={{
            width: 18,
            height: 18,
            transform: value ? "translateX(20px)" : "translateX(2px)",
          }}
        />
      </div>
    </label>
  )
}
