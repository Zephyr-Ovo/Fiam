import { motion, AnimatePresence } from "framer-motion"
import { useEffect } from "react"

type Props = {
  open: boolean
  title?: string
  message?: string
  cancelLabel?: string
  confirmLabel?: string
  onCancel: () => void
  onConfirm: () => void
}

const INK = "#3f2f29"

/**
 * Frosted-glass confirm modal. English Cancel / Yes by default.
 * Backdrop blurs the chat behind. Returns focus on close is the caller's job.
 */
export function ConfirmModal({
  open,
  title,
  message,
  cancelLabel = "Cancel",
  confirmLabel = "Yes",
  onCancel,
  onConfirm,
}: Props) {
  // ESC = cancel, Enter = confirm
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel()
      else if (e.key === "Enter") onConfirm()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onCancel, onConfirm])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="absolute inset-0 z-30 grid place-items-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          // Block mousedown on the entire modal so the chat textarea
          // doesn't lose focus (which would dismiss the soft keyboard).
          onMouseDown={(e) => e.preventDefault()}
        >
          {/* frosted backdrop */}
          <button
            type="button"
            onClick={onCancel}
            className="absolute inset-0"
            aria-label="Dismiss"
            style={{
              background: "rgba(63,47,41,0.28)",
            }}
          />
          {/* dialog */}
          <motion.div
            initial={{ y: 8, scale: 0.97 }}
            animate={{ y: 0, scale: 1 }}
            exit={{ y: 8, scale: 0.97 }}
            transition={{ duration: 0.16, ease: [0.2, 0, 0.2, 1] }}
            className="relative w-[260px] overflow-hidden rounded-2xl"
            style={{
              background: "rgba(255,250,240,0.97)",
              border: "1px solid rgba(176,139,127,0.28)",
              boxShadow:
                "0 1px 0 rgba(255,255,255,0.85) inset, 0 24px 60px -16px rgba(63,47,41,0.45)",
            }}
          >
            <div className="px-5 pt-4 pb-3">
              {title && (
                <div
                  className="mb-1.5 text-[15px] font-medium"
                  style={{ color: INK, fontFamily: "var(--font-sans)" }}
                >
                  {title}
                </div>
              )}
              {message && (
                <div
                  className="whitespace-pre-line text-[13px] leading-[1.45]"
                  style={{
                    color: "rgba(63,47,41,0.72)",
                    fontFamily: "var(--font-sans)",
                  }}
                >
                  {message}
                </div>
              )}
            </div>
            <div
              className="grid grid-cols-2 border-t"
              style={{ borderColor: "rgba(176,139,127,0.22)" }}
            >
              <button
                type="button"
                onClick={onCancel}
                className="py-2.5 text-[14px] transition-colors hover:bg-black/5"
                style={{
                  color: "rgba(63,47,41,0.7)",
                  fontFamily: "var(--font-sans)",
                  borderRight: "1px solid rgba(176,139,127,0.22)",
                }}
              >
                {cancelLabel}
              </button>
              <button
                type="button"
                onClick={onConfirm}
                className="py-2.5 text-[14px] font-medium transition-colors hover:bg-black/5"
                style={{ color: INK, fontFamily: "var(--font-sans)" }}
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
