// Robust image picker. The Android WebView <input type=file> chooser is
// flaky across Capacitor/WebView versions (it silently fails to open the
// system file manager). On native we use the Capacitor Camera plugin's
// gallery picker, which always opens the OS photo picker. On web we fall
// back to a real <input type=file> click.

import { Camera, CameraResultType, CameraSource } from "@capacitor/camera"

function isNative(): boolean {
  // @ts-expect-error Capacitor injects this global on device
  return !!(typeof window !== "undefined" && window.Capacitor?.isNativePlatform?.())
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

// Returns a downscaled JPEG data URL, or null if the user cancelled.
export async function pickImageDataUrl(maxDim = 1200): Promise<string | null> {
  let raw: string | null = null
  if (isNative()) {
    const photo = await Camera.getPhoto({
      source: CameraSource.Photos,
      resultType: CameraResultType.DataUrl,
      quality: 90,
      correctOrientation: true,
    }).catch(() => null)
    raw = photo?.dataUrl ?? null
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
