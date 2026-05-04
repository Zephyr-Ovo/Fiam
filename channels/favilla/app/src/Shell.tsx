import { useEffect, useState } from "react"
import App from "./App"
import { Home, type HomeTarget } from "./routes/Home"
import { Settings } from "./routes/Settings"
import { appConfig } from "./config"
import { installGlobalTapHaptics } from "./lib/haptics"
import { App as CapApp } from "@capacitor/app"
import { LocalNotifications } from "@capacitor/local-notifications"

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

function blurActiveInput() {
  const active = document.activeElement
  if (active instanceof HTMLElement && active !== document.body) active.blur()
}

export default function Shell() {
  const [page, setPage] = useState<Page>("home")
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [phone, setPhone] = useState(isPhoneish())
  const [unread, setUnread] = useState(false)

  useEffect(() => {
    installGlobalTapHaptics()
    const mq = window.matchMedia("(max-width: 480px)")
    const onChange = () => setPhone(isPhoneish())
    mq.addEventListener("change", onChange)
    return () => mq.removeEventListener("change", onChange)
  }, [])

  // App fires this whenever the assistant adds/updates a reply. Mark unread
  // unless the chat view is currently visible. Also raise a system local
  // notification when the app is in background / not in chat — same UX as
  // any other chat app.
  useEffect(() => {
    function onReply(ev: Event) {
      if (page !== "chat") setUnread(true)
      // Only fire system notification when the app is not foreground+chat.
      const e = ev as CustomEvent<{ peerName?: string; preview?: string }>
      const peer = e.detail?.peerName || appConfig.aiName || "Favilla"
      const preview = (e.detail?.preview || "").trim().slice(0, 120) || "New reply"
      const appHidden =
        typeof document !== "undefined" && document.visibilityState === "hidden"
      if (appHidden || page !== "chat") {
        if (isNative()) {
          // best-effort; ignore failures (permission denied etc.)
          void LocalNotifications.schedule({
            notifications: [
              {
                id: Date.now() % 2147483647,
                title: peer,
                body: preview,
                smallIcon: "ic_launcher",
                extra: { route: "chat" },
              },
            ],
          }).catch(() => undefined)
        }
      }
    }
    window.addEventListener("favilla:newAiReply", onReply as EventListener)
    return () =>
      window.removeEventListener("favilla:newAiReply", onReply as EventListener)
  }, [page])

  // Clear unread when user enters chat.
  useEffect(() => {
    if (page === "chat") setUnread(false)
  }, [page])

  // Request notification permission once on native, and route notification
  // taps directly to the chat view.
  useEffect(() => {
    if (!isNative()) return
    void LocalNotifications.requestPermissions().catch(() => undefined)
    let handle: { remove: () => void } | null = null
    let cancelled = false
    void LocalNotifications.addListener(
      "localNotificationActionPerformed",
      () => {
        // Always navigate to chat — there is only one chat target right now.
        setPage("chat")
        setUnread(false)
      },
    ).then((h) => {
      if (cancelled) h.remove()
      else handle = h
    })
    return () => {
      cancelled = true
      handle?.remove()
    }
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
        blurActiveInput()
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
  // While Settings is open, hide Home from compositing so the backdrop-filter
  // blur doesn't have to repaint the whole collage every frame (this was the
  // dominant source of the "paints in waves" lag).
  const isChat = page === "chat"
  const slide = "transform 420ms cubic-bezier(0.22, 1, 0.36, 1)"
  const inner = (
    <>
      <div
        className="absolute inset-0 h-full w-full"
        style={{
          transform: isChat ? "translate3d(-12%,0,0)" : "translate3d(0,0,0)",
          transition: slide,
          pointerEvents: isChat ? "none" : "auto",
          willChange: "transform",
        }}
      >
        <Home onNavigate={onHomeNavigate} unread={unread} />
      </div>
      <div
        className="absolute inset-0 h-full w-full"
        style={{
          transform: isChat ? "translate3d(0,0,0)" : "translate3d(100%,0,0)",
          transition: slide,
          pointerEvents: isChat ? "auto" : "none",
          willChange: "transform",
        }}
      >
        <App onBack={() => { blurActiveInput(); setPage("home") }} />
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
