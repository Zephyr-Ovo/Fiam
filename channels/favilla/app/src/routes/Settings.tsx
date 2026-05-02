import { motion, AnimatePresence } from "framer-motion"
import { useEffect, useState } from "react"
import { appConfig, saveConfig, type AppConfig } from "../config"

type Props = {
  open: boolean
  onClose: () => void
}

/**
 * Settings — iOS-Settings-app inspired sheet. Whole phone frame blurs;
 * a 70%-height sheet rises from the bottom and holds *grouped* frosted
 * cards (one per logical section). Tap the dimmed area above to dismiss.
 */
export function Settings({ open, onClose }: Props) {
  // Local snapshot — only commit on Save.
  const [draft, setDraft] = useState<AppConfig>(appConfig)
  useEffect(() => {
    if (open) setDraft({ ...appConfig })
  }, [open])

  function commit() {
    saveConfig(draft)
    onClose()
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="absolute inset-0 z-40"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {/* full-screen frosted backdrop, click anywhere outside the sheet to dismiss */}
          <button
            type="button"
            aria-label="Close settings"
            onClick={onClose}
            className="absolute inset-0 cursor-default"
            style={{
              background: "rgba(63,47,41,0.18)",
            }}
          />

          {/* bottom sheet, ~78% height */}
          <motion.div
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 32 }}
            className="absolute inset-x-0 bottom-0 flex flex-col overflow-hidden"
            style={{
              height: "78%",
              borderTopLeftRadius: 28,
              borderTopRightRadius: 28,
              background: "rgba(245,238,228,0.98)",
              borderTop: "1px solid rgba(255,255,255,0.5)",
              boxShadow: "0 -10px 40px -10px rgba(63,47,41,0.35)",
            }}
          >
            {/* header row (no drag indicator — sheet is not draggable) */}
            <div className="flex items-center justify-between px-5 pt-5 pb-3">
              <button
                type="button"
                onClick={onClose}
                className="text-[14px] font-normal"
                style={{ color: "rgba(63,47,41,0.65)", fontFamily: "var(--font-sans)" }}
              >
                Cancel
              </button>
              <div
                className="text-[15px] font-semibold"
                style={{ color: "#3f2f29", fontFamily: "var(--font-sans)" }}
              >
                Settings
              </div>
              <button
                type="button"
                onClick={commit}
                className="text-[14px] font-semibold"
                style={{ color: "#c9824a", fontFamily: "var(--font-sans)" }}
              >
                Save
              </button>
            </div>

            {/* scrolling grouped cards */}
            <div className="flex-1 overflow-y-auto px-4 pb-6 pt-2">
              {/* Names — split into two horizontal cards */}
              <div className="mb-5">
                <div
                  className="mb-1.5 px-3 text-[11px] uppercase tracking-wider"
                  style={{ color: "rgba(63,47,41,0.55)", fontFamily: "var(--font-sans)" }}
                >
                  Names
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <SoloCard>
                    <SoloField
                      label="AI"
                      value={draft.aiName}
                      onChange={(v) => setDraft({ ...draft, aiName: v })}
                    />
                  </SoloCard>
                  <SoloCard>
                    <SoloField
                      label="You"
                      value={draft.userName}
                      onChange={(v) => setDraft({ ...draft, userName: v })}
                    />
                  </SoloCard>
                </div>
              </div>

              <Group title="Backend">
                <Row
                  label="API base"
                  value={draft.apiBase}
                  onChange={(v) => setDraft({ ...draft, apiBase: v })}
                  placeholder="https://fiet.cc"
                />
                <Row
                  label="Ingest token"
                  value={draft.ingestToken}
                  onChange={(v) => setDraft({ ...draft, ingestToken: v })}
                  placeholder="X-Fiam-Token"
                  secret
                />
                <Row
                  label="OpenRouter key"
                  value={draft.openrouterKey}
                  onChange={(v) => setDraft({ ...draft, openrouterKey: v })}
                  placeholder="sk-or-v1-..."
                  secret
                />
              </Group>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** Frosted grouped section, à la iOS Settings table group. */
function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <div
        className="mb-1.5 px-3 text-[11px] uppercase tracking-wider"
        style={{ color: "rgba(63,47,41,0.55)", fontFamily: "var(--font-sans)" }}
      >
        {title}
      </div>
      <div
        className="overflow-hidden rounded-2xl"
        style={{
          background: "rgba(255,250,243,0.85)",
          border: "1px solid rgba(255,255,255,0.45)",
          boxShadow: "0 1px 0 rgba(255,255,255,0.55) inset, 0 6px 18px -8px rgba(63,47,41,0.2)",
        }}
      >
        {children}
      </div>
    </div>
  )
}

/** Single row inside a Group: label on the left, input on the right. */
function Row({
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
    <label
      className="flex items-center gap-3 border-b px-4 py-3 last:border-b-0"
      style={{ borderColor: "rgba(176,139,127,0.18)" }}
    >
      <span
        className="shrink-0 text-[13px]"
        style={{ color: "#3f2f29", fontFamily: "var(--font-sans)", minWidth: 96 }}
      >
        {label}
      </span>
      <input
        type={secret ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        className="min-w-0 flex-1 bg-transparent text-right text-[13px] outline-none"
        style={{ color: "rgba(63,47,41,0.78)", fontFamily: "var(--font-sans)" }}
      />
    </label>
  )
}

/** Single-field card (used for the side-by-side Names cards). */
function SoloCard({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="overflow-hidden rounded-2xl px-4 py-3"
      style={{
        background: "rgba(255,250,243,0.85)",
        border: "1px solid rgba(255,255,255,0.45)",
        boxShadow: "0 1px 0 rgba(255,255,255,0.55) inset, 0 6px 18px -8px rgba(63,47,41,0.2)",
      }}
    >
      {children}
    </div>
  )
}

function SoloField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <label className="block">
      <div
        className="text-[10px] uppercase tracking-wider"
        style={{ color: "rgba(63,47,41,0.5)", fontFamily: "var(--font-sans)" }}
      >
        {label}
      </div>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        className="mt-0.5 w-full bg-transparent text-[14px] outline-none"
        style={{ color: "#3f2f29", fontFamily: "var(--font-sans)" }}
      />
    </label>
  )
}
