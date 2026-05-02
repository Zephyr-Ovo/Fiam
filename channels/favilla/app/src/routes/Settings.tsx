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
      {/* dim backdrop — semi-transparent so the home page is still faintly
          visible behind. Click-outside-to-close. NO mousedown preventDefault
          here — that was killing input focus on the card. */}
      <button
        type="button"
        aria-label="Dismiss settings"
        onClick={onClose}
        className="absolute inset-0"
        style={{
          background: visible ? "rgba(40, 28, 22, 0.45)" : "rgba(40, 28, 22, 0)",
          transition: "background 200ms ease-out",
          border: 0,
          padding: 0,
          cursor: "default",
        }}
      />
      {/* card — sibling of backdrop, centered absolutely over it */}
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
          // frosted cream glass \u2014 genuinely translucent so the dimmed
          // home behind shows through. backdrop-filter does the heavy
          // lifting; the rgba background is intentionally low-alpha.
          background: "rgba(250, 244, 229, 0.42)",
          backdropFilter: "blur(22px) saturate(140%)",
          WebkitBackdropFilter: "blur(22px) saturate(140%)",
          border: "1px solid rgba(255, 255, 255, 0.6)",
          boxShadow:
            "0 18px 50px -12px rgba(40, 28, 22, 0.45), 0 1px 0 rgba(255,255,255,0.7) inset",
          padding: "20px 22px 16px",
          color: "#3f2f29",
          fontFamily: "var(--font-sans)",
          opacity: visible ? 1 : 0,
          transition:
            "opacity 200ms ease-out, transform 220ms cubic-bezier(0.22, 1, 0.36, 1)",
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
