import { motion } from "framer-motion"
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
}

export function Home({ onNavigate }: Props) {
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
          <motion.img
            key={`img-${s.key}`}
            src={s.asset}
            alt=""
            className="pointer-events-none absolute select-none"
            draggable={false}
            animate={{ scale: down ? 0.97 : 1, y: down ? 2 : 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 22 }}
            style={{
              left: s.x * SCALE,
              top: s.y * SCALE,
              width: s.w * SCALE,
              height: s.h * SCALE,
              transformOrigin: "center",
              willChange: "transform",
            }}
          />
        )
      })}
      {SLOTS.map((s) => (
        <PressButton
          key={`hit-${s.key}`}
          ariaLabel={s.label}
          onClick={() => {
            // Let the press shrink+release animation play out before navigating.
            window.setTimeout(() => onNavigate(s.go), 90)
          }}
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
        onClick={() => window.setTimeout(() => onNavigate("settings"), 90)}
        style={{
          left: 35 * SCALE,
          top: 56 * SCALE,
          width: 31 * SCALE,
          height: 33 * SCALE,
        }}
      >
        <img src="/home/setting.png" alt="" className="h-full w-full" draggable={false} />
      </PressButton>

      {/* strollentry — "↑ Escaping my desk…" header text. Tap → walking */}
      <PressButton
        ariaLabel="Go for a walk"
        onClick={() => window.setTimeout(() => onNavigate("walking"), 90)}
        style={{
          left: 131 * SCALE,
          top: 67 * SCALE,
          width: 173 * SCALE,
          height: 20 * SCALE,
        }}
      >
        <img src="/home/strollentry.png" alt="" className="h-full w-full" draggable={false} />
      </PressButton>
      </div>
    </div>
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
  return (
    <motion.button
      type="button"
      aria-label={ariaLabel}
      onClick={onClick}
      onTapStart={onPressStart}
      onTap={onPressEnd}
      onTapCancel={onPressEnd}
      whileTap={{ scale: 0.97, y: 2 }}
      transition={{ type: "spring", stiffness: 400, damping: 22 }}
      className="absolute cursor-pointer border-0 bg-transparent p-0 outline-none focus-visible:ring-2 focus-visible:ring-amber-200/60"
      style={{
        ...style,
        background: DEBUG_HITBOX ? "rgba(255,0,0,0.22)" : undefined,
        outline: DEBUG_HITBOX ? "1px dashed rgba(200,0,0,0.7)" : undefined,
        filter: shadow ? "drop-shadow(0 8px 14px rgba(63,47,41,0.28)) drop-shadow(0 2px 4px rgba(63,47,41,0.18))" : undefined,
      }}
    >
      {children}
    </motion.button>
  )
}
