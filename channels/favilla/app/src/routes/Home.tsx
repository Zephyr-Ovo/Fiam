import { useEffect, useRef, useState, type CSSProperties } from "react"

/**
 * Home — collage of paper cutouts. Layout coords come from the Figma
 * frame `home` (412×915). Several elements intentionally overflow the
 * phone edges; the parent clips them.
 */

const SCALE = 1 // phone frame is 412 wide; figma frame is 412 wide
const DEBUG_HITBOX = false // tint button overlays for tuning

type Slot = {
  key: string
  asset: string
  // visual rect (image)
  x: number
  y: number
  w: number
  h: number
  // hit rect (overlay button); independent of visual
  hx: number
  hy: number
  hw: number
  hh: number
  go: HomeTarget
  label: string
}

export type HomeTarget =
  | "chat"
  | "settings"
  | "moments"
  | "dashboard"
  | "reading"
  | "walking"
  | "easter"

// Render bottom→top (later items overlap earlier ones)
const SLOTS: Slot[] = [
  { key: "gallery",   asset: "/home/gallery.png",   x: 264, y: 188, w: 195, h: 195, hx: 284, hy: 188, hw: 175, hh: 195, go: "moments",   label: "Moments captured" },
  { key: "studio",    asset: "/home/studio.png",    x: 184, y: 416, w: 266, h: 374, hx: 214, hy: 454, hw: 266, hh: 289, go: "reading",   label: "Brewing — reading" },
  { key: "chat",      asset: "/home/chat.png",      x: -42, y: 134, w: 392, h: 338, hx:   5, hy: 178, hw: 299, hh: 250, go: "chat",      label: "Open chat" },
  { key: "dashbroad", asset: "/home/dashbroad.png", x: -40, y: 441, w: 273, h: 271, hx:   4, hy: 484, hw: 186, hh: 185, go: "dashboard", label: "To-do dashboard" },
  { key: "eastenegg", asset: "/home/eastenegg.png", x:  78, y: 323, w: 264, h: 256, hx: 144, hy: 387, hw: 132, hh: 128, go: "easter",    label: "Easter egg" },
]

type Props = {
  onNavigate: (t: HomeTarget) => void
  /** Show a small red dot on the message icon (top-right) when there are unread chats. */
  unread?: boolean
}

export function Home({ onNavigate, unread = false }: Props) {
  // Track which slot is currently being pressed so the visible image can
  // shrink in sync with the (separate) hit overlay.
  const [pressed, setPressed] = useState<string | null>(null)
  // Scale-to-fit: design canvas is 412×915. On real phones (or after status bar)
  // the available height differs — we scale uniformly so the whole collage fits.
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [scale, setScale] = useState(1)
  useEffect(() => {
    function fit() {
      const el = wrapRef.current
      if (!el) return
      const w = el.clientWidth, h = el.clientHeight
      if (!w || !h) return
      // Width-fit: design 412dp must fill the full phone width. Vertical
      // overflow is clipped by overflow-hidden — the collage has no critical
      // content past 800dp.
      setScale(w / 412)
      void h
    }
    fit()
    window.addEventListener("resize", fit)
    const ro = new ResizeObserver(fit)
    if (wrapRef.current) ro.observe(wrapRef.current)
    return () => { window.removeEventListener("resize", fit); ro.disconnect() }
  }, [])
  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden">
      <div
        className="absolute"
        style={{
          left: 0,
          top: 0,
          width: 412,
          height: 915,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
        }}
      >
      {/* background collage layer (texture + flowers) */}
      <img
        src="/home/bg.png"
        alt=""
        className="pointer-events-none absolute select-none"
        style={{
          left: 0 * SCALE,
          top: -65 * SCALE,
          width: 692 * SCALE,
          height: 1172 * SCALE,
          objectFit: "cover",
        }}
        draggable={false}
      />

      {/* main collage groups (z-order matches Figma layer order). Visual
          images are decoration only; click areas are separate overlays so we
          can shrink/shift hitboxes without disturbing the artwork. */}
      {SLOTS.map((s) => {
        const down = pressed === s.key
        return (
          <img
            key={`img-${s.key}`}
            src={s.asset}
            alt=""
            className="pointer-events-none absolute select-none"
            draggable={false}
            style={{
              left: s.x * SCALE,
              top: s.y * SCALE,
              width: s.w * SCALE,
              height: s.h * SCALE,
              transformOrigin: "center",
              willChange: "transform, filter",
              transform: down ? "scale(0.97) translateY(2px)" : "scale(1) translateY(0)",
              filter: down
                ? "drop-shadow(0 2px 4px rgba(63,47,41,0.18))"
                : "drop-shadow(0 8px 12px rgba(63,47,41,0.28))",
              transition:
                "transform 180ms cubic-bezier(0.34, 1.56, 0.64, 1), filter 140ms ease-out",
            }}
          />
        )
      })}
      {SLOTS.map((s) => (
        <PressButton
          key={`hit-${s.key}`}
          ariaLabel={s.label}
          onClick={() => onNavigate(s.go)}
          onPressStart={() => setPressed(s.key)}
          onPressEnd={() => setPressed((p) => (p === s.key ? null : p))}
          style={{
            left: s.hx * SCALE,
            top: s.hy * SCALE,
            width: s.hw * SCALE,
            height: s.hh * SCALE,
          }}
        />
      ))}

      {/* setting button (top layer, top-left) */}
      <PressButton
        ariaLabel="Settings"
        onClick={() => onNavigate("settings")}
        style={{
          left: 35 * SCALE,
          top: 56 * SCALE,
          width: 31 * SCALE,
          height: 33 * SCALE,
        }}
      >
        <img src="/home/setting.png" alt="" className="h-full w-full" draggable={false} />
      </PressButton>

      {/* message button (top layer, top-right) — symmetric to settings; small
          red dot when there are unread chats. Tap → chat. Vertically
          compressed to ~80% so the bell icon doesn't feel oversized next to
          the settings sticker. */}
      <PressButton
        ariaLabel="Open chat (messages)"
        onClick={() => onNavigate("chat")}
        style={{
          left: (412 - 35 - 31) * SCALE,
          top: (56 + 3) * SCALE,
          width: 31 * SCALE,
          height: 27 * SCALE,
        }}
      >
        <div className="relative h-full w-full">
          <img
            src="/home/message.png"
            alt=""
            className="h-full w-full"
            style={{ objectFit: "contain" }}
            draggable={false}
          />
          {unread && (
            <span
              className="absolute"
              style={{
                top: 1 * SCALE,
                right: 1 * SCALE,
                width: 8 * SCALE,
                height: 8 * SCALE,
                borderRadius: "50%",
                background: "#e0563a",
                boxShadow: "0 0 0 1.5px rgba(255,250,243,0.95)",
              }}
              aria-hidden="true"
            />
          )}
        </div>
      </PressButton>

      <StrollHandle onOpen={() => onNavigate("walking")} />
      </div>
    </div>
  )
}

function StrollHandle({ onOpen }: { onOpen: () => void }) {
  const startRef = useRef<{ x: number; y: number; pointerId: number } | null>(null)
  const pullRef = useRef(0)
  const [pull, setPull] = useState(0)

  function reset() {
    startRef.current = null
    pullRef.current = 0
    setPull(0)
  }

  return (
    <button
      type="button"
      aria-label="Pull down to open Stroll"
      className="absolute border-0 bg-transparent p-0 outline-none"
      style={{
        left: 96 * SCALE,
        top: 49 * SCALE,
        width: 220 * SCALE,
        height: 58 * SCALE,
        transform: `translateY(${Math.min(pull, 70) * 0.18}px)`,
        transition: pull ? "none" : "transform 180ms ease-out",
        touchAction: "none",
      }}
      onPointerDown={(e) => {
        if (e.pointerType === "mouse" && e.button !== 0) return
        startRef.current = { x: e.clientX, y: e.clientY, pointerId: e.pointerId }
        e.currentTarget.setPointerCapture?.(e.pointerId)
      }}
      onPointerMove={(e) => {
        const start = startRef.current
        if (!start) return
        const dx = e.clientX - start.x
        const dy = e.clientY - start.y
        if (Math.abs(dx) > 44 && Math.abs(dx) > dy) {
          reset()
          return
        }
        const next = Math.max(0, Math.min(dy, 140))
        pullRef.current = next
        setPull(next)
      }}
      onPointerUp={(e) => {
        e.preventDefault()
        if (pullRef.current >= 72) onOpen()
        reset()
      }}
      onPointerCancel={reset}
      onClick={() => onOpen()}
    >
      <img
        src="/home/strollentry.png"
        alt=""
        className="absolute select-none"
        style={{
          left: 35 * SCALE,
          top: 18 * SCALE,
          width: 173 * SCALE,
          height: 20 * SCALE,
        }}
        draggable={false}
      />
    </button>
  )
}

function PressButton({
  children,
  ariaLabel,
  onClick,
  onPressStart,
  onPressEnd,
  style,
  shadow = false,
}: {
  children?: React.ReactNode
  ariaLabel: string
  onClick: () => void
  onPressStart?: () => void
  onPressEnd?: () => void
  style: CSSProperties
  shadow?: boolean
}) {
  // Three-phase choreography (independent of press duration):
  //   1. press-down: scale to 0.96 over PRESS_MS
  //   2. release rebound: scale back to 1.0 over REBOUND_MS
  //   3. fire onClick AFTER rebound finishes
  const PRESS_MS = 80
  const REBOUND_MS = 180
  const [pressed, setPressed] = useState(false)
  const armedRef = useRef(false) // true between pointerDown and committed pointerUp
  const fireTimerRef = useRef<number | null>(null)

  function clearFire() {
    if (fireTimerRef.current !== null) {
      window.clearTimeout(fireTimerRef.current)
      fireTimerRef.current = null
    }
  }

  function handleDown(e: React.PointerEvent<HTMLButtonElement>) {
    e.currentTarget.setPointerCapture?.(e.pointerId)
    armedRef.current = true
    setPressed(true)
    onPressStart?.()
  }

  function handleUp() {
    if (!armedRef.current) return
    armedRef.current = false
    setPressed(false) // triggers CSS transition back to 1.0 over REBOUND_MS
    onPressEnd?.()
    clearFire()
    fireTimerRef.current = window.setTimeout(() => {
      fireTimerRef.current = null
      onClick()
    }, REBOUND_MS)
  }

  function handleCancel() {
    if (!armedRef.current) return
    armedRef.current = false
    setPressed(false)
    onPressEnd?.()
    // No onClick fire on cancel.
  }

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      onPointerDown={handleDown}
      onPointerUp={handleUp}
      onPointerCancel={handleCancel}
      className="absolute cursor-pointer border-0 bg-transparent p-0 outline-none focus-visible:ring-2 focus-visible:ring-amber-200/60"
      style={{
        ...style,
        background: DEBUG_HITBOX ? "rgba(255,0,0,0.22)" : undefined,
        outline: DEBUG_HITBOX ? "1px dashed rgba(200,0,0,0.7)" : undefined,
        filter: shadow ? "drop-shadow(0 8px 14px rgba(63,47,41,0.28)) drop-shadow(0 2px 4px rgba(63,47,41,0.18))" : undefined,
        transform: pressed ? "scale(0.96) translateY(2px)" : "scale(1) translateY(0)",
        transition: pressed
          ? `transform ${PRESS_MS}ms cubic-bezier(0.4, 0, 0.6, 1)`
          : `transform ${REBOUND_MS}ms cubic-bezier(0.34, 1.56, 0.64, 1)`,
        touchAction: "manipulation",
      }}
    >
      {children}
    </button>
  )
}
