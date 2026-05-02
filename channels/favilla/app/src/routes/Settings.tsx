import { useEffect, useState } from "react"
import { appConfig, saveConfig, type AppConfig } from "../config"

type Props = {
  open: boolean
  onClose: () => void
}

/**
 * Settings — single-layer popover. Cool slate-ink card on a dim backdrop.
 * No white panel, no per-input boxes — just labels + bottom-line inputs.
 *
 * Performance notes for low-end Android WebView:
 *   - Drop backdrop-filter (real-time blur is the source of the "layered
 *     paint" jank the user reported).
 *   - Animate the WHOLE popup as one transform+opacity layer; do NOT stagger
 *     children. CSS transition only, no framer-motion, no rAF gating.
 *   - Block mousedown on the entire card so tapping inputs/buttons never
 *     blurs the chat textarea (matches ConfirmModal behaviour).
 */
export function Settings({ open, onClose }: Props) {
  const [draft, setDraft] = useState<AppConfig>(appConfig)
  const [mounted, setMounted] = useState(open)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (open) {
      setDraft({ ...appConfig })
      setMounted(true)
      // next tick so the initial (collapsed) state paints once before the
      // transition flips visible -> true. Single rAF, not double.
      const id = window.requestAnimationFrame(() => setVisible(true))
      return () => window.cancelAnimationFrame(id)
    } else if (mounted) {
      setVisible(false)
      const id = window.setTimeout(() => setMounted(false), 180)
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
        background: visible ? "rgba(14, 20, 32, 0.55)" : "rgba(14, 20, 32, 0)",
        transition: "background 180ms ease-out",
        pointerEvents: visible ? "auto" : "none",
      }}
      onClick={onClose}
      onMouseDown={(e) => e.preventDefault()}
    >
      <div
        role="dialog"
        aria-label="Settings"
        onClick={(e) => e.stopPropagation()}
        className="relative flex flex-col"
        style={{
          width: "86%",
          maxWidth: 360,
          borderRadius: 22,
          background: "rgba(36, 46, 62, 0.92)",
          border: "1px solid rgba(255,255,255,0.08)",
          boxShadow:
            "0 18px 50px -12px rgba(0,0,0,0.55), 0 1px 0 rgba(255,255,255,0.06) inset",
          padding: "20px 22px 16px",
          color: "rgba(236, 240, 248, 0.92)",
          fontFamily: "var(--font-sans)",
          opacity: visible ? 1 : 0,
          transform: visible ? "scale(1) translateY(0)" : "scale(0.96) translateY(6px)",
          transition:
            "opacity 180ms ease-out, transform 180ms cubic-bezier(0.22, 1, 0.36, 1)",
          willChange: "transform, opacity",
        }}
      >
        <div
          className="mb-4 text-center text-[15px] font-medium tracking-wide"
          style={{ color: "rgba(236, 240, 248, 0.95)" }}
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
              color: "rgba(236, 240, 248, 0.75)",
              background: "rgba(255,255,255,0.06)",
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={commit}
            className="rounded-full px-5 py-1.5 text-[13px] font-medium"
            style={{
              color: "#1a1f2a",
              background: "#e7c98a",
              boxShadow: "0 1px 0 rgba(255,255,255,0.4) inset",
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
        borderBottom: last ? "none" : "1px solid rgba(255,255,255,0.08)",
      }}
    >
      <div
        className="text-[10.5px] uppercase tracking-[0.08em]"
        style={{ color: "rgba(180, 192, 212, 0.7)" }}
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
          color: "rgba(236, 240, 248, 0.95)",
          fontFamily: "var(--font-sans)",
          // Subtle focus ring via underline color shift, no box border.
          boxShadow: focused
            ? "inset 0 -1px 0 rgba(231, 201, 138, 0.7)"
            : "inset 0 -1px 0 rgba(255,255,255,0.05)",
          transition: "box-shadow 140ms ease-out",
        }}
      />
    </label>
  )
}
