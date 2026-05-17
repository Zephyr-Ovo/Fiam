// Robust image picker. The Android WebView <input type=file> chooser is
// flaky across Capacitor/WebView versions (it silently fails to open the
// system file manager). On native we use the Capacitor Camera plugin's
// gallery picker, which always opens the OS photo picker. On web we fall
// back to a real <input type=file> click.

import { Capacitor } from "@capacitor/core"
import { Camera, CameraResultType, CameraSource } from "@capacitor/camera"

// Authoritative platform check: the imported Capacitor API is populated by
// the time module code runs, unlike the injected `window.Capacitor` global
// which is bridge-timing-dependent and was making device builds fall through
// to the broken WebView <input> path (the "picker won't open" bug).
function isNative(): boolean {
  try {
    return Capacitor.isNativePlatform()
  } catch {
    return false
  }
}

// True only for the genuine user-cancelled-the-picker case (Capacitor Camera
// rejects with "User cancelled photos app"), so a real permission/plugin
// failure is not silently swallowed as "no selection".
function isCancellation(err: unknown): boolean {
  const msg = (err instanceof Error ? err.message : String(err || "")).toLowerCase()
  return msg.includes("cancel") || msg.includes("no image picked")
}

function downscaleDataUrl(src: string, maxDim = 1200, quality = 0.78): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      let w = img.width
      let h = img.height
      if (w > maxDim || h > maxDim) {
        const s = maxDim / Math.max(w, h)
        w = Math.round(w * s)
        h = Math.round(h * s)
      }
      const canvas = document.createElement("canvas")
      canvas.width = w
      canvas.height = h
      canvas.getContext("2d")!.drawImage(img, 0, 0, w, h)
      resolve(canvas.toDataURL("image/jpeg", quality))
    }
    img.onerror = () => reject(new Error("decode failed"))
    img.src = src
  })
}

function pickViaInput(): Promise<string | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input")
    input.type = "file"
    input.accept = "image/*"
    input.style.position = "fixed"
    input.style.left = "-9999px"
    document.body.appendChild(input)
    input.onchange = () => {
      const file = input.files?.[0]
      document.body.removeChild(input)
      if (!file) return resolve(null)
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result || "") || null)
      reader.onerror = () => resolve(null)
      reader.readAsDataURL(file)
    }
    input.click()
  })
}

async function pickViaCamera(): Promise<string | null> {
  // On Android 13+ source:Photos uses the system Photo Picker (no runtime
  // permission). On older OS the plugin needs the photos permission; request
  // it explicitly so the picker actually opens instead of no-opping.
  try {
    const perm = await Camera.checkPermissions()
    if (perm.photos !== "granted" && perm.photos !== "limited") {
      const req = await Camera.requestPermissions({ permissions: ["photos"] })
      if (req.photos !== "granted" && req.photos !== "limited") {
        throw new Error("photo library permission denied")
      }
    }
  } catch (e) {
    // checkPermissions is unsupported on some platforms — fall through and let
    // getPhoto surface the real outcome rather than blocking the picker.
    if (e instanceof Error && e.message.includes("denied")) throw e
  }
  const photo = await Camera.getPhoto({
    source: CameraSource.Photos,
    resultType: CameraResultType.DataUrl,
    quality: 90,
    correctOrientation: true,
  })
  return photo?.dataUrl ?? null
}

// Returns a downscaled JPEG data URL, or null if the user cancelled. Throws
// on a genuine failure (permission/plugin) so the caller can show feedback
// instead of the picker appearing to do nothing.
export async function pickImageDataUrl(maxDim = 1200): Promise<string | null> {
  let raw: string | null = null
  if (isNative()) {
    try {
      raw = await pickViaCamera()
    } catch (err) {
      if (isCancellation(err)) return null
      throw err instanceof Error ? err : new Error(String(err))
    }
  } else {
    raw = await pickViaInput()
  }
  if (!raw) return null
  try {
    return await downscaleDataUrl(raw, maxDim)
  } catch {
    return raw
  }
}
