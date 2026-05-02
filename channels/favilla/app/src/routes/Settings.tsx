import { useEffect, useState } from "react"
import { appConfig, saveConfig, type AppConfig } from "../config"

type Props = {
  open: boolean
  onClose: () => void
}

/**
 * Settings — centered fixed frosted card. Hardcoded layout: backdrop blur,
 * single panel with a few inputs and Cancel/Save. CSS-only fade in/out
 * (~120ms). No framer-motion. No bottom sheet. No scrolling content.
 */
export function Settings({ open, onClose }: Props) {
  // Local snapshot — only commit on Save.
  const [draft, setDraft] = useState<AppConfig>(appConfig)
  // Mount latches so we can run the fade-out before unmounting.
  const [mounted, setMounted] = useState(open)
  const [visible, setVisible] = useState(open)

  useEffect(() => {
    if (open) {
      setDraft({ ...appConfig })
      setMounted(true)
      // next frame → opacity 1
      const id = window.requestAnimationFrame(() => setVisible(true))
      return () => window.cancelAnimationFrame(id)
    } else if (mounted) {
      setVisible(false)
      const id = window.setTimeout(() => setMounted(false), 130)
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
      className="absolute inset-0 z-40 flex items-center justify-center"
      style={{
        opacity: visible ? 1 : 0,
        transition: "opacity 120ms ease-out",
        pointerEvents: visible ? "auto" : "none",
      }}
    >
      {/* dim backdrop — no blur, just darken. Click to dismiss */}
      <button
        type="button"
        aria-label="Close settings"
        onClick={onClose}
        className="absolute inset-0 cursor-default border-0"
        style={{
          background: "rgba(0,0,0,0.45)",
        }}
      />

      {/* centered fixed frosted card */}
      <div
        className="relative flex flex-col overflow-hidden"
        style={{
          width: "85%",
          maxWidth: 360,
          borderRadius: 22,
          background: "rgba(255,250,243,0.55)",
          backdropFilter: "blur(20px) saturate(140%)",
          WebkitBackdropFilter: "blur(20px) saturate(140%)",
          border: "1px solid rgba(255,255,255,0.55)",
          boxShadow: "0 18px 50px -12px rgba(0,0,0,0.5)",
          padding: "18px 18px 14px",
        }}
      >
        <div
          className="mb-3 text-center text-[15px] font-semibold"
          style={{ color: "#3f2f29", fontFamily: "var(--font-sans)" }}
        >
          Settings
        </div>

        <Field
          label="AI name"
          value={draft.aiName}
          onChange={(v) => setDraft({ ...draft, aiName: v })}
        />
        <Field
          label="Your name"
          value={draft.userName}
          onChange={(v) => setDraft({ ...draft, userName: v })}
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
          label="OpenRouter key"
          value={draft.openrouterKey}
          onChange={(v) => setDraft({ ...draft, openrouterKey: v })}
          placeholder="sk-or-v1-..."
          secret
        />

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-4 py-1.5 text-[13px]"
            style={{
              color: "rgba(63,47,41,0.7)",
              fontFamily: "var(--font-sans)",
              background: "rgba(176,139,127,0.12)",
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={commit}
            className="rounded-full px-4 py-1.5 text-[13px] font-semibold"
            style={{
              color: "#fff",
              fontFamily: "var(--font-sans)",
              background: "#c9824a",
            }}
          >
            Save
          </button>
        </div>
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
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  secret?: boolean
}) {
  return (
    <label className="mb-2 block">
      <div
        className="mb-0.5 text-[10px] uppercase tracking-wider"
        style={{ color: "rgba(63,47,41,0.55)", fontFamily: "var(--font-sans)" }}
      >
        {label}
      </div>
      <input
        type={secret ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        className="w-full rounded-lg px-2.5 py-1.5 text-[13px] outline-none"
        style={{
          color: "#3f2f29",
          fontFamily: "var(--font-sans)",
          background: "rgba(255,250,243,0.85)",
          border: "1px solid rgba(176,139,127,0.25)",
        }}
      />
    </label>
  )
}
