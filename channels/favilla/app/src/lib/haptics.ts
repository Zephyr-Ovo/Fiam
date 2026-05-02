// Light haptic on tap.
// Uses navigator.vibrate (Android Chromium WebView supports it).
// No-op when unsupported (iOS WebView, desktop).

export function tap(ms = 8) {
  try {
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate(ms)
    }
  } catch {
    /* ignore */
  }
}

/** Install a global pointerdown listener that vibrates on any <button> tap. */
export function installGlobalTapHaptics() {
  if (typeof window === "undefined") return
  const handler = (e: PointerEvent) => {
    const t = e.target as Element | null
    if (!t) return
    if (t.closest('button, [role="button"], a[href]')) tap(8)
  }
  window.addEventListener("pointerdown", handler, { passive: true, capture: true })
}
