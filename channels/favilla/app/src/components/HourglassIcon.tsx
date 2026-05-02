type Props = {
  className?: string
  active?: boolean
  filled?: boolean
  /** 0..1 — how much sand to show in the TOP bulb (long-press progress).
   *  Ignored when `active` is true (sand animation overrides). */
  fillProgress?: number
  sandColor?: string
  /** Same outer size as RecallIcon (14×14) so it slots into the same button. */
  size?: number
  cycleSeconds?: number
}

const SAND = "#FAEC8C"

export function HourglassIcon({
  className,
  active = false,
  filled = false,
  fillProgress = 1,
  sandColor = SAND,
  size = 14,
  cycleSeconds = 2,
}: Props) {
  const stroke = "currentColor"
  const baseFill = "none"
  const showSand = active || filled
  // Top bulb sand height grows from bottom up to top as fillProgress climbs.
  // Bulb spans y in [0, 7]; bottom of bulb = y=7. Sand rect height = 7 * p.
  const p = active ? 1 : Math.max(0, Math.min(1, fillProgress))
  const topRectY = 7 - 7 * p
  const topRectH = 7 * p
  return (
    <svg
      width={Math.round((size * 12) / 14)}
      height={size}
      viewBox="0 0 12 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <defs>
        <clipPath id="hg-top-bulb">
          <path d="M9.5 3.5C9.5 4.42826 9.13125 5.3185 8.47487 5.97487C7.8185 6.63125 6.92826 7 6 7C5.07174 7 4.1815 6.63125 3.52513 5.97487C2.86875 5.3185 2.5 4.42826 2.5 3.5V0.5H9.5V3.5Z" />
        </clipPath>
        <clipPath id="hg-bottom-bulb">
          <path d="M9.5 10.5C9.5 9.57174 9.13125 8.6815 8.47487 8.02513C7.8185 7.36875 6.92826 7 6 7C5.07174 7 4.1815 7.36875 3.52513 8.02513C2.86875 8.6815 2.5 9.57174 2.5 10.5V13.5H9.5V10.5Z" />
        </clipPath>
      </defs>
      <path
        d="M9.5 3.5C9.5 4.42826 9.13125 5.3185 8.47487 5.97487C7.8185 6.63125 6.92826 7 6 7C5.07174 7 4.1815 6.63125 3.52513 5.97487C2.86875 5.3185 2.5 4.42826 2.5 3.5V0.5H9.5V3.5Z"
        fill={baseFill}
      />
      <path
        d="M9.5 10.5C9.5 9.57174 9.13125 8.6815 8.47487 8.02513C7.8185 7.36875 6.92826 7 6 7C5.07174 7 4.1815 7.36875 3.52513 8.02513C2.86875 8.6815 2.5 9.57174 2.5 10.5V13.5H9.5V10.5Z"
        fill={baseFill}
      />
      {showSand && (
        <>
          <g clipPath="url(#hg-top-bulb)">
            <rect
              x="0"
              y={active ? 0 : topRectY}
              width="12"
              height={active ? 7 : topRectH}
              fill={sandColor}
              style={active ? ({ animation: `hg-drain ${cycleSeconds}s ease-in-out infinite`, transformOrigin: "6px 0.5px" } as React.CSSProperties) : undefined}
            />
          </g>
          <g clipPath="url(#hg-bottom-bulb)">
            <rect
              x="0"
              y="7"
              width="12"
              height={active ? 7 : 0}
              fill={sandColor}
              style={active ? ({ animation: `hg-fill ${cycleSeconds}s ease-in-out infinite`, transformOrigin: "6px 13.5px" } as React.CSSProperties) : undefined}
            />
          </g>
          {active && (
            <line
              x1="6"
              y1="6.45"
              x2="6"
              y2="7.55"
              stroke={sandColor}
              strokeWidth={0.7}
              strokeLinecap="round"
              style={{ animation: `hg-stream ${cycleSeconds}s ease-in-out infinite` }}
            />
          )}
        </>
      )}
      <path
        d="M9.5 3.5C9.5 4.42826 9.13125 5.3185 8.47487 5.97487C7.8185 6.63125 6.92826 7 6 7C5.07174 7 4.1815 6.63125 3.52513 5.97487C2.86875 5.3185 2.5 4.42826 2.5 3.5V0.5H9.5V3.5Z"
        fill="none"
        stroke={stroke}
        strokeWidth="1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M9.5 10.5C9.5 9.57174 9.13125 8.6815 8.47487 8.02513C7.8185 7.36875 6.92826 7 6 7C5.07174 7 4.1815 7.36875 3.52513 8.02513C2.86875 8.6815 2.5 9.57174 2.5 10.5V13.5H9.5V10.5Z"
        fill="none"
        stroke={stroke}
        strokeWidth="1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M0.5 0.5H11.5"
        stroke={stroke}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M0.5 13.5H11.5"
        stroke={stroke}
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      <style>{`
        @keyframes hg-drain {
          0% { transform: scaleY(1); }
          88% { transform: scaleY(0); }
          100% { transform: scaleY(1); }
        }
        @keyframes hg-fill {
          0% { transform: scaleY(0); }
          88% { transform: scaleY(1); }
          100% { transform: scaleY(0); }
        }
        @keyframes hg-stream {
          0%, 100% { opacity: 0; }
          12%, 84% { opacity: 1; }
        }
      `}</style>
    </svg>
  )
}
