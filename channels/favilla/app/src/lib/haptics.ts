// Light haptic on tap.
// Prefer @capacitor/haptics on native (real haptic actuator).
// Fallback to navigator.vibrate on web/WebView (Android Chromium honors it
// when the AndroidManifest has VIBRATE permission).

import { Haptics, ImpactStyle } from "@capacitor/haptics"

function isNative(): boolean {
  // @ts-expect-error capacitor injects global at runtime
  return !!(typeof window !== "undefined" && window.Capacitor?.isNativePlatform?.())
}

export function tap(ms = 18) {
  try {
    if (isNative()) {
      void Haptics.impact({ style: ImpactStyle.Medium })
      return
    }
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
