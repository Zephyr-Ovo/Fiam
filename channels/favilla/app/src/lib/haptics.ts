// Light haptic on tap.
// Uses the browser vibration path only. Calling Capacitor's native Haptics
// bridge on every pointerdown made the first tap on many controls visibly lag.

export function tap(ms = 18) {
  try {
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate(ms)
    }
  } catch {
    /* ignore */
  }
}

/** Install a global pointerdown listener that fires a haptic on any tappable element. */
export function installGlobalTapHaptics() {
  if (typeof window === "undefined") return
  const handler = (e: PointerEvent) => {
    const t = e.target as Element | null
    if (!t) return
    if (t.closest('button, [role="button"], a[href], textarea, input')) tap(18)
  }
  window.addEventListener("pointerdown", handler, { passive: true, capture: true })
}
