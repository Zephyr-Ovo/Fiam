import { ArrowLeft, Camera, Mic, Pause, Phone, Radio, Square, Video } from "lucide-react"
import type { ReactNode } from "react"

const INK = "#1E2843"
const PAPER = "#F4D6CC"
const PEACH = "#EDAB98"
const WATER = "#90C5BF"
const BLUE = "#5E74A0"

type Props = {
  onBack: () => void
}

const chatter = [
  { name: "Fiet", text: "light changed at the next crossing" },
  { name: "you", text: "keep live, no photo yet" },
  { name: "Fiet", text: "xiao is showing the walking face" },
]

export function Stroll({ onBack }: Props) {
  return (
    <div
      className="relative flex h-full w-full flex-col overflow-hidden"
      style={{ background: `linear-gradient(180deg, ${PAPER} 0%, #E1D4CC 42%, #B4BAC6 100%)` }}
    >
      <header className="relative z-20 flex items-center justify-between px-4 pb-2 pt-[calc(env(safe-area-inset-top)+8px)]">
        <button
          type="button"
          onClick={onBack}
          className="grid h-9 w-9 place-items-center rounded-full"
          style={{ color: INK, background: "rgba(255,255,255,0.28)" }}
          aria-label="Back"
        >
          <ArrowLeft className="h-5 w-5" strokeWidth={1.8} />
        </button>
        <div className="flex flex-col items-center leading-none">
          <span className="text-[18px] font-medium" style={{ color: INK, fontFamily: "var(--font-serif)" }}>
            Stroll
          </span>
          <span className="mt-0.5 text-[10px] uppercase tracking-[0.18em]" style={{ color: "rgba(30,40,67,0.58)", fontFamily: "var(--font-sans)" }}>
            limen live
          </span>
        </div>
        <div className="h-9 w-9" aria-hidden="true" />
      </header>

      <section className="relative z-10 px-3">
        <div
          className="relative aspect-[4/3] w-full overflow-hidden rounded-[8px]"
          style={{
            background:
              "linear-gradient(135deg, rgba(30,40,67,0.96), rgba(94,116,160,0.72) 48%, rgba(144,197,191,0.78))",
            boxShadow: "0 18px 42px -24px rgba(30,40,67,0.72)",
          }}
        >
          <div className="absolute inset-0 opacity-35" style={{
            backgroundImage:
              "linear-gradient(90deg, rgba(255,255,255,0.12) 1px, transparent 1px), linear-gradient(0deg, rgba(255,255,255,0.12) 1px, transparent 1px)",
            backgroundSize: "34px 34px",
          }} />
          <div className="absolute left-4 top-4 flex items-center gap-2 rounded-full px-3 py-1.5" style={{ background: "rgba(1,3,1,0.36)", color: "#fff" }}>
            <Video className="h-3.5 w-3.5" strokeWidth={1.8} />
            <span className="text-[11px] font-medium uppercase tracking-[0.18em]">live</span>
          </div>
          <div className="absolute left-4 bottom-4 flex flex-col gap-2">
            <Segment leftIcon={<Mic className="h-3.5 w-3.5" />} left="Voice" rightIcon={<Phone className="h-3.5 w-3.5" />} right="Call" active="left" />
            <Segment leftIcon={<Radio className="h-3.5 w-3.5" />} left="Live" rightIcon={<Camera className="h-3.5 w-3.5" />} right="Photo" active="left" />
          </div>
          <div
            className="absolute bottom-4 right-4 grid h-[76px] w-[76px] place-items-center rounded-full"
            style={{
              background: `radial-gradient(circle at 38% 32%, #CBE8E9, ${WATER} 42%, ${BLUE} 100%)`,
              border: "3px solid rgba(255,255,255,0.68)",
              boxShadow: "0 12px 30px -14px rgba(1,3,1,0.75)",
            }}
            aria-label="Xiao screen preview"
          >
            <div className="h-9 w-9 rounded-full" style={{ background: "rgba(30,40,67,0.8)", boxShadow: `0 -8px 0 ${PEACH} inset` }} />
          </div>
        </div>
      </section>

      <section className="relative mt-3 min-h-0 flex-1 overflow-hidden">
        <MapPanel />
        <div className="pointer-events-none absolute left-4 bottom-[96px] flex w-[72%] flex-col gap-2">
          {chatter.map((line) => (
            <div key={`${line.name}-${line.text}`} className="text-[13px] leading-[1.35]" style={{ color: "rgba(1,3,1,0.76)", fontFamily: "var(--font-sans)", textShadow: "0 1px 8px rgba(244,214,204,0.95)" }}>
              <span className="font-medium" style={{ color: INK }}>{line.name}</span>
              <span> {line.text}</span>
            </div>
          ))}
        </div>
      </section>

      <div className="absolute inset-x-0 bottom-0 z-20 px-4 pb-[calc(env(safe-area-inset-bottom)+14px)] pt-5" style={{ background: "linear-gradient(180deg, transparent, rgba(180,186,198,0.86) 44%, rgba(180,186,198,0.96))" }}>
        <div className="flex items-center gap-3">
          <button type="button" className="flex h-12 flex-1 items-center justify-center gap-2 rounded-full text-[14px] font-medium" style={{ background: "rgba(255,255,255,0.52)", color: INK, fontFamily: "var(--font-sans)" }}>
            <Pause className="h-4 w-4" strokeWidth={1.8} />
            Pause
          </button>
          <button type="button" className="flex h-12 flex-1 items-center justify-center gap-2 rounded-full text-[14px] font-medium" style={{ background: "rgba(30,40,67,0.88)", color: "#F4D6CC", fontFamily: "var(--font-sans)" }}>
            <Square className="h-3.5 w-3.5" strokeWidth={1.9} />
            End
          </button>
        </div>
      </div>
    </div>
  )
}

function Segment({ leftIcon, left, rightIcon, right, active }: { leftIcon: ReactNode; left: string; rightIcon: ReactNode; right: string; active: "left" | "right" }) {
  return (
    <div className="flex w-fit items-center rounded-full p-1" style={{ background: "rgba(1,3,1,0.28)", color: "rgba(255,255,255,0.76)" }}>
      <button type="button" className="flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-medium" style={{ background: active === "left" ? "rgba(244,214,204,0.92)" : "transparent", color: active === "left" ? INK : "rgba(255,255,255,0.78)" }}>
        {leftIcon}
        {left}
      </button>
      <button type="button" className="flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[11px] font-medium" style={{ background: active === "right" ? "rgba(244,214,204,0.92)" : "transparent", color: active === "right" ? INK : "rgba(255,255,255,0.78)" }}>
        {rightIcon}
        {right}
      </button>
    </div>
  )
}

function MapPanel() {
  return (
    <div className="absolute inset-0 overflow-hidden" style={{ background: "#CBE8E9" }}>
      <div className="absolute inset-0" style={{
        backgroundImage:
          "linear-gradient(90deg, rgba(38,57,110,0.12) 1px, transparent 1px), linear-gradient(0deg, rgba(38,57,110,0.12) 1px, transparent 1px)",
        backgroundSize: "44px 44px",
      }} />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 412 430" preserveAspectRatio="none" aria-hidden="true">
        <path d="M-20 296 C 92 248, 118 136, 214 156 C 284 171, 294 82, 438 44" fill="none" stroke="rgba(255,255,255,0.78)" strokeWidth="34" strokeLinecap="round" />
        <path d="M-20 296 C 92 248, 118 136, 214 156 C 284 171, 294 82, 438 44" fill="none" stroke="rgba(94,116,160,0.46)" strokeWidth="3" strokeDasharray="8 10" strokeLinecap="round" />
        <path d="M54 430 C 92 332, 158 278, 252 254 C 324 236, 370 196, 432 124" fill="none" stroke="rgba(255,255,255,0.54)" strokeWidth="22" strokeLinecap="round" />
        <path d="M54 430 C 92 332, 158 278, 252 254 C 324 236, 370 196, 432 124" fill="none" stroke="rgba(134,87,123,0.36)" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <div className="absolute left-[53%] top-[42%] h-4 w-4 rounded-full" style={{ background: PEACH, border: "3px solid rgba(255,255,255,0.92)", boxShadow: "0 0 0 5px rgba(237,171,152,0.28)" }} />
    </div>
  )
}