import { useEffect, useState } from "react"
import { appConfig, saveConfig, type AppConfig } from "../config"

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
          width: "86%",
          maxWidth: 360,
          borderRadius: 22,
          background: "rgba(255, 250, 243, 0.55)",
          backdropFilter: "blur(20px) saturate(150%)",
          WebkitBackdropFilter: "blur(20px) saturate(150%)",
          border: "1px solid rgba(255, 255, 255, 0.6)",
          boxShadow:
            "0 18px 50px -12px rgba(40, 28, 22, 0.45), 0 1px 0 rgba(255,255,255,0.7) inset",
          padding: "20px 22px 16px",
          color: "#3f2f29",
          fontFamily: "var(--font-sans)",
          opacity: visible ? 1 : 0,
          transition:
            "opacity 120ms ease-out, transform 120ms ease-out",
          willChange: "transform, opacity",
        }}
      >
        <div
          className="mb-4 text-center text-[15px] font-medium tracking-wide"
          style={{ color: "#3f2f29" }}
        >
          Settings
        </div>

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
          onChange={(v) => setDraft({ ...draft, defaultRuntime: v })}
        />
        <ColorField
          label="Your bubble"
          value={draft.userBubbleBg}
          onChange={(v) => setDraft({ ...draft, userBubbleBg: v })}
          placeholder="rgba(208,188,190,0.92)"
        />
        <ColorField
          label="AI bubble"
          value={draft.agentBubbleBg}
          onChange={(v) => setDraft({ ...draft, agentBubbleBg: v })}
          placeholder="rgba(245,245,245,0.88)"
        />
        <Field
          label="Background URL"
          value={draft.bg}
          onChange={(v) => setDraft({ ...draft, bg: v })}
          placeholder="/bg.jpg or https://…"
          last
        />

        <div className="mt-5 flex justify-end gap-2">
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

// ColorField — text input for any CSS color (rgba/hex/named) plus a small
// swatch+native picker on the right. Picker only sets opaque hex; users who
// want alpha keep typing in the text box. Live-previews into the swatch as
// you type so you can see the color before committing.
function ColorField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  const [focused, setFocused] = useState(false)
  // Native <input type=color> only accepts #RRGGBB. Try to derive one from
  // the current value; fall back to a sensible default if it's rgba/named.
  const hex = /^#([0-9a-f]{6})$/i.test(value) ? value : "#d0bcbe"
  return (
    <label
      className="block"
      style={{
        paddingTop: 8,
        paddingBottom: 8,
        borderBottom: "1px solid rgba(63, 47, 41, 0.12)",
      }}
    >
      <div
        className="text-[10.5px] uppercase tracking-[0.08em]"
        style={{ color: "rgba(63, 47, 41, 0.55)" }}
      >
        {label}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={placeholder}
          spellCheck={false}
          className="flex-1 bg-transparent px-0 py-1 text-[14px] outline-none"
          style={{
            color: "#3f2f29",
            fontFamily: "var(--font-mono, var(--font-sans))",
            boxShadow: focused
              ? "inset 0 -1px 0 rgba(176, 139, 127, 0.85)"
              : "inset 0 -1px 0 rgba(63, 47, 41, 0.05)",
            transition: "box-shadow 140ms ease-out",
          }}
        />
        <span
          aria-hidden
          style={{
            display: "inline-block",
            width: 22,
            height: 22,
            borderRadius: 6,
            background: value || placeholder || "#fff",
            border: "1px solid rgba(63,47,41,0.18)",
            position: "relative",
            overflow: "hidden",
          }}
        >
          <input
            type="color"
            value={hex}
            onChange={(e) => onChange(e.target.value)}
            aria-label={`${label} color picker`}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              opacity: 0,
              cursor: "pointer",
              border: 0,
              padding: 0,
            }}
          />
        </span>
      </div>
    </label>
  )
}