type Props = {
  className?: string
  active?: boolean
  filled?: boolean
  sandColor?: string
  /** Same outer size as RecallIcon (14×14) so it slots into the same button. */
  size?: number
  /**
   * One full sand-flow cycle in seconds. The animation runs forever while
   * `active` is true and stops the moment the parent sets `active={false}`
   * (i.e. the backend signalled "event sealed"). The per-cycle duration is
   * just visual cadence — the *real* duration is dictated by the parent.
   */
  cycleSeconds?: number
}

const SAND = "#FAEC8C"

/**
 * Hourglass icon with real sand-flow animation. The top bulb empties while
 * the bottom bulb fills, then loops. Pure CSS keyframes — no rotation.
 */
export function HourglassIcon({
  className,
  active = false,
  filled = false,
  sandColor = SAND,
  size = 14,
  cycleSeconds = 2,
}: Props) {
  const stroke = "currentColor"
  const showSand = active || filled
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 14 14"
      fill="none"
      className={className}
    >
      <defs>
        <clipPath id="hg-top-bulb">
          <polygon points="3.6,2 10.4,2 7,6.6" />
        </clipPath>
        <clipPath id="hg-bot-bulb">
          <polygon points="3.6,12 10.4,12 7,7.4" />
        </clipPath>
      </defs>

      {showSand && (
        <>
          {/* Sand inside top bulb — drains from full to empty */}
          <g clipPath="url(#hg-top-bulb)">
            <rect
              x="3"
              y="2"
              width="8"
              height="5"
              fill={sandColor}
              style={
                active
                  ? ({
                      animation: `hg-drain ${cycleSeconds}s ease-in-out infinite`,
                      transformOrigin: "7px 2px",
                    } as React.CSSProperties)
                  : undefined
              }
            />
          </g>

          {/* Sand inside bottom bulb — fills from empty to full */}
          <g clipPath="url(#hg-bot-bulb)">
            <rect
              x="3"
              y="7"
              width="8"
              height="5"
              fill={sandColor}
              style={
                active
                  ? ({
                      animation: `hg-fill ${cycleSeconds}s ease-in-out infinite`,
                      transformOrigin: "7px 12px",
                    } as React.CSSProperties)
                  : undefined
              }
            />
          </g>
        </>
      )}

      {/* Falling stream in the neck */}
      {active && (
        <line
          x1="7"
          y1="6.4"
          x2="7"
          y2="7.6"
          stroke={sandColor}
          strokeWidth={0.7}
          strokeLinecap="round"
          style={{
            animation: `hg-stream ${cycleSeconds}s ease-in-out infinite`,
          }}
        />
      )}

      {/* Frame on top so sand never overflows it visually */}
      <path
        d="M3 1.5h8M3 12.5h8M3.5 1.5v2.2c0 1 0.4 1.95 1.1 2.65L7 7l-2.4 2.65a3.75 3.75 0 0 0-1.1 2.65v0.2M10.5 1.5v2.2c0 1-0.4 1.95-1.1 2.65L7 7l2.4 2.65c0.7 0.7 1.1 1.65 1.1 2.65v0.2"
        stroke={stroke}
        strokeWidth={1}
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      <style>{`
        @keyframes hg-drain {
          0%   { transform: scaleY(1); }
          90%  { transform: scaleY(0); }
          100% { transform: scaleY(1); }
        }
        @keyframes hg-fill {
          0%   { transform: scaleY(0); }
          90%  { transform: scaleY(1); }
          100% { transform: scaleY(0); }
        }
        @keyframes hg-stream {
          0%, 100% { opacity: 0; }
          15%, 85% { opacity: 1; }
        }
      `}</style>
    </svg>
  )
}
