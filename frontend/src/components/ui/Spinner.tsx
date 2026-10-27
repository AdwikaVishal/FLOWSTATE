interface SpinnerProps {
  size?: number
  label?: string
}

export function Spinner({ size = 16, label }: SpinnerProps) {
  return (
    <span className="spinner" role="status" aria-label={label ?? 'Loading'}>
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2.5" />
        <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    </span>
  )
}