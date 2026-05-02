type Props = {
  className?: string
  active?: boolean
  filled?: boolean
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
  sandColor = SAND,
  size = 14,
  cycleSeconds = 2,
}: Props) {
  const stroke = "currentColor"
  const fill = active || filled ? sandColor : "white"
  return (
    <svg
      width={Math.round((size * 12) / 14)}
      height={size}
      viewBox="0 0 12 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <path
        d="M9.5 3.5C9.5 4.42826 9.13125 5.3185 8.47487 5.97487C7.8185 6.63125 6.92826 7 6 7C5.07174 7 4.1815 6.63125 3.52513 5.97487C2.86875 5.3185 2.5 4.42826 2.5 3.5V0.5H9.5V3.5Z"
        fill={fill}
        stroke={stroke}
        strokeWidth="1"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={active ? ({ animation: `hg-fill-pulse ${cycleSeconds}s ease-in-out infinite` } as React.CSSProperties) : undefined}
      />
      <path
        d="M9.5 10.5C9.5 9.57174 9.13125 8.6815 8.47487 8.02513C7.8185 7.36875 6.92826 7 6 7C5.07174 7 4.1815 7.36875 3.52513 8.02513C2.86875 8.6815 2.5 9.57174 2.5 10.5V13.5H9.5V10.5Z"
        fill={fill}
        stroke={stroke}
        strokeWidth="1"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={active ? ({ animation: `hg-fill-pulse ${cycleSeconds}s ease-in-out infinite reverse` } as React.CSSProperties) : undefined}
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
        @keyframes hg-fill-pulse {
          0%, 100% { fill-opacity: 0.78; }
          50% { fill-opacity: 1; }
        }
      `}</style>
    </svg>
  )
}
