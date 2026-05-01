import { useState } from "react"
import App from "./App"
import { Home, type HomeTarget } from "./routes/Home"
import { Settings } from "./routes/Settings"
import { appConfig } from "./config"

type Page = "home" | "chat"

/**
 * Shell — phone frame + page state machine. State machine instead of a router
 * because Capacitor has no URL bar; one less dependency.
 */
export default function Shell() {
  const [page, setPage] = useState<Page>("home")
  const [settingsOpen, setSettingsOpen] = useState(false)

  function onHomeNavigate(t: HomeTarget) {
    if (t === "settings") {
      setSettingsOpen(true)
      return
    }
    if (t === "chat") {
      setPage("chat")
      return
    }
    // moments / dashboard / reading / walking / easter — no navigation yet.
    // eslint-disable-next-line no-console
    console.info("[home] target not yet wired:", t)
  }

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
        {page === "home" && <Home onNavigate={onHomeNavigate} />}
        {page === "chat" && <App onBack={() => setPage("home")} />}

        <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      </div>
    </div>
  )
}
