import { useEffect, useState } from "react"
import App from "./App"
import { Home, type HomeTarget } from "./routes/Home"
import { Settings } from "./routes/Settings"
import { appConfig } from "./config"
import { installGlobalTapHaptics } from "./lib/haptics"
import { App as CapApp } from "@capacitor/app"

type Page = "home" | "chat"

/**
 * On real device (Capacitor) or any narrow viewport we drop the desktop
 * "phone frame" and render full-bleed. Frame is only for desktop preview.
 */
function isPhoneish() {
  if (typeof window === "undefined") return false
  // @ts-expect-error — Capacitor injects a global on native.
  if (window.Capacitor?.isNativePlatform?.()) return true
  return window.matchMedia("(max-width: 480px)").matches
}

function isNative(): boolean {
  // @ts-expect-error capacitor injects global at runtime
  return !!(typeof window !== "undefined" && window.Capacitor?.isNativePlatform?.())
}

export default function Shell() {
  const [page, setPage] = useState<Page>("home")
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [phone, setPhone] = useState(isPhoneish())

  useEffect(() => {
    installGlobalTapHaptics()
    const mq = window.matchMedia("(max-width: 480px)")
    const onChange = () => setPhone(isPhoneish())
    mq.addEventListener("change", onChange)
    return () => mq.removeEventListener("change", onChange)
  }, [])

  // Hardware / system back button — match top-left back button:
  //   home + settings open  -> close settings
  //   home (nothing open)   -> minimize app (do NOT exit, keeps services alive)
  //   any other page        -> go back to home
  useEffect(() => {
    if (!isNative()) return
    let handle: { remove: () => void } | null = null
    let cancelled = false
    void CapApp.addListener("backButton", () => {
      if (settingsOpen) {
        setSettingsOpen(false)
        return
      }
      if (page !== "home") {
        setPage("home")
        return
      }
      // On home: send to background instead of killing process
      void CapApp.minimizeApp()
    }).then((h) => {
      if (cancelled) h.remove()
      else handle = h
    })
    return () => {
      cancelled = true
      handle?.remove()
    }
  }, [page, settingsOpen])

  function onHomeNavigate(t: HomeTarget) {
    if (t === "settings") {
      setSettingsOpen(true)
      return
    }
    if (t === "chat") {
      setPage("chat")
      return
    }
    // eslint-disable-next-line no-console
    console.info("[home] target not yet wired:", t)
  }

  // Render Home + App together; toggle visibility instead of unmount so
  // returning to Home is instant and big assets aren't re-decoded.
  const isChat = page === "chat"
  const slide = "transform 220ms cubic-bezier(0.22, 1, 0.36, 1)"
  const inner = (
    <>
      <div
        className="absolute inset-0 h-full w-full"
        style={{
          transform: isChat ? "translateX(-18%)" : "translateX(0)",
          transition: slide,
          pointerEvents: isChat ? "none" : "auto",
        }}
      >
        <Home onNavigate={onHomeNavigate} />
      </div>
      <div
        className="absolute inset-0 h-full w-full"
        style={{
          transform: isChat ? "translateX(0)" : "translateX(100%)",
          transition: slide,
          pointerEvents: isChat ? "auto" : "none",
        }}
      >
        <App onBack={() => setPage("home")} />
      </div>
      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  )

  if (phone) {
    // Full-bleed on real device: no padding, no frame, no outer wallpaper.
    return (
      <div className="relative h-dvh w-screen overflow-hidden">{inner}</div>
    )
  }

  // Desktop preview frame.
  return (
    <div
      className="flex min-h-dvh items-center justify-center bg-cover bg-center bg-fixed"
      style={{
        backgroundImage: `url(${appConfig.bg})`,
        padding: "clamp(0px, 4vw, 32px)",
      }}
    >
      <div
        className="relative overflow-hidden"
        style={{
          width: "min(100%, 412px)",
          height: "min(100dvh, 915px)",
          borderRadius: 28,
          boxShadow:
            "0 0 0 1px rgba(255,255,255,0.18), 0 30px 80px -20px rgba(0,0,0,0.45), 0 10px 30px -10px rgba(0,0,0,0.3)",
        }}
      >
        {inner}
      </div>
    </div>
  )
}
