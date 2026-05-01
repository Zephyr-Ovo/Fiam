// Streamline Core: interface-lock--combination-combo-lock-locked-padlock-secure-security-shield-keyhole
// Provided by Zephyr from Figma. 11x14 viewBox. Stroke inherits currentColor.
type Props = {
  className?: string
  strokeWidth?: number
}

export function LockIcon({ className, strokeWidth = 1 }: Props) {
  return (
    <svg
      viewBox="0 0 11 14"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      className={className}
      aria-hidden="true"
    >
      <path d="M9.5 5.5H1.5C0.947715 5.5 0.5 5.94772 0.5 6.5V12.5C0.5 13.0523 0.947715 13.5 1.5 13.5H9.5C10.0523 13.5 10.5 13.0523 10.5 12.5V6.5C10.5 5.94772 10.0523 5.5 9.5 5.5Z" />
      <path d="M9 5.5V4C9 3.07174 8.63125 2.1815 7.97487 1.52513C7.3185 0.868749 6.42826 0.5 5.5 0.5C4.57174 0.5 3.6815 0.868749 3.02513 1.52513C2.36875 2.1815 2 3.07174 2 4V5.5" />
      <path d="M5.5 10C5.77614 10 6 9.77614 6 9.5C6 9.22386 5.77614 9 5.5 9C5.22386 9 5 9.22386 5 9.5C5 9.77614 5.22386 10 5.5 10Z" />
    </svg>
  )
}
