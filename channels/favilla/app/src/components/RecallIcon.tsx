type Props = {
  className?: string
  strokeWidth?: number
  fill?: string
}

export function RecallIcon({ className, strokeWidth = 1, fill = "none" }: Props) {
  return (
    <svg
      width="12"
      height="14"
      viewBox="0 0 12 14"
      fill={fill}
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      <path
        d="M9.5 3.5C9.5 4.42826 9.13125 5.3185 8.47487 5.97487C7.8185 6.63125 6.92826 7 6 7C5.07174 7 4.1815 6.63125 3.52513 5.97487C2.86875 5.3185 2.5 4.42826 2.5 3.5V0.5H9.5V3.5Z"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M9.5 10.5C9.5 9.57174 9.13125 8.6815 8.47487 8.02513C7.8185 7.36875 6.92826 7 6 7C5.07174 7 4.1815 7.36875 3.52513 8.02513C2.86875 8.6815 2.5 9.57174 2.5 10.5V13.5H9.5V10.5Z"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M0.5 0.5H11.5"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M0.5 13.5H11.5"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
