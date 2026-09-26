import type { ReactNode } from 'react'

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = '',
  bodyClassName = 'card-pad',
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-hairline px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-secondary">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  )
}

/** A single headline number. No plot, so no hover layer. */
export function StatTile({
  label,
  value,
  unit,
  detail,
  tone = 'neutral',
}: {
  label: string
  value: ReactNode
  unit?: string
  detail?: ReactNode
  tone?: 'neutral' | 'good' | 'warning' | 'critical'
}) {
  const toneClass = {
    neutral: 'text-ink',
    good: 'text-good',
    warning: 'text-warning',
    critical: 'text-critical',
  }[tone]
  return (
    <div className="card card-pad">
      <div className="label">{label}</div>
      <div className={`mt-1 flex items-baseline gap-1 ${toneClass}`}>
        <span className="text-2xl font-semibold leading-none">{value}</span>
        {unit && <span className="text-sm text-ink-secondary">{unit}</span>}
      </div>
      {detail && <div className="mt-1 text-xs text-ink-secondary">{detail}</div>}
    </div>
  )
}

/**
 * Magnitude against a known maximum: one sequential hue, 4px rounded data end,
 * always directly labelled (never colour alone).
 */
export function Meter({
  label,
  used,
  total,
  unit = '',
  format,
}: {
  label: string
  used: number
  total: number
  unit?: string
  format?: (value: number) => string
}) {
  const safeTotal = total > 0 ? total : 1
  const pct = Math.min(100, Math.max(0, (used / safeTotal) * 100))
  const show = format ?? ((value: number) => `${Math.round(value)}`)
  const tone =
    pct >= 90 ? 'var(--status-critical)' : pct >= 75 ? 'var(--status-warning)' : 'var(--seq-450)'
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-ink-secondary">{label}</span>
        <span className="tabular text-xs text-ink">
          {show(used)} / {show(total)}
          {unit && ` ${unit}`} <span className="text-muted">({pct.toFixed(0)}%)</span>
        </span>
      </div>
      <div
        className="mt-1.5 h-2 w-full overflow-hidden rounded-full"
        style={{ background: 'var(--gridline)' }}
        role="img"
        aria-label={`${label}: ${show(used)} of ${show(total)} ${unit} used`}
      >
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${pct}%`, background: tone }}
        />
      </div>
    </div>
  )
}

const STATUS_TONE: Record<string, { bg: string; fg: string; icon: string }> = {
  real: { bg: 'rgba(12,163,12,0.12)', fg: 'var(--status-good)', icon: '●' },
  connected: { bg: 'rgba(12,163,12,0.12)', fg: 'var(--status-good)', icon: '●' },
  running: { bg: 'rgba(12,163,12,0.12)', fg: 'var(--status-good)', icon: '●' },
  healthy: { bg: 'rgba(12,163,12,0.12)', fg: 'var(--status-good)', icon: '●' },
  simulated: { bg: 'rgba(250,178,25,0.16)', fg: 'var(--status-warning)', icon: '◐' },
  mock: { bg: 'rgba(250,178,25,0.16)', fg: 'var(--status-warning)', icon: '◐' },
  compat: { bg: 'rgba(236,131,90,0.16)', fg: 'var(--status-serious)', icon: '◑' },
  experimental: { bg: 'rgba(236,131,90,0.16)', fg: 'var(--status-serious)', icon: '◑' },
  unavailable: { bg: 'rgba(137,135,129,0.16)', fg: 'var(--text-muted)', icon: '○' },
  disconnected: { bg: 'rgba(137,135,129,0.16)', fg: 'var(--text-muted)', icon: '○' },
  disabled: { bg: 'rgba(137,135,129,0.16)', fg: 'var(--text-muted)', icon: '○' },
  none: { bg: 'rgba(137,135,129,0.16)', fg: 'var(--text-muted)', icon: '○' },
  error: { bg: 'rgba(208,59,59,0.14)', fg: 'var(--status-critical)', icon: '✕' },
  failed: { bg: 'rgba(208,59,59,0.14)', fg: 'var(--status-critical)', icon: '✕' },
}

/** Status is never colour alone: every badge carries an icon and a label. */
export function StatusBadge({ value, title }: { value: string; title?: string }) {
  const key = (value || '').toLowerCase()
  const tone = STATUS_TONE[key] ?? {
    bg: 'rgba(137,135,129,0.14)',
    fg: 'var(--text-secondary)',
    icon: '·',
  }
  return (
    <span
      className="chip border-transparent uppercase"
      style={{ background: tone.bg, color: tone.fg }}
      title={title ?? value}
    >
      <span aria-hidden>{tone.icon}</span>
      {value}
    </span>
  )
}

const STATE_TONE: Record<string, string> = {
  DRAFT: 'var(--text-muted)',
  RECOMMENDED: 'var(--series-1)',
  WAITING_FOR_HUMAN: 'var(--status-warning)',
  APPROVED: 'var(--series-1)',
  PLANNED: 'var(--series-1)',
  WAITING_FOR_FINAL_APPROVAL: 'var(--status-warning)',
  DEPLOYING: 'var(--series-2)',
  RUNNING: 'var(--status-good)',
  REJECTED: 'var(--text-muted)',
  FAILED: 'var(--status-critical)',
  ROLLING_BACK: 'var(--status-serious)',
  ROLLED_BACK: 'var(--text-muted)',
}

export function StateBadge({ state }: { state: string }) {
  const color = STATE_TONE[state] ?? 'var(--text-secondary)'
  const human = state.includes('WAITING') ? '⏸' : state === 'RUNNING' ? '●' : '▸'
  return (
    <span
      className="chip border-transparent"
      style={{ background: 'var(--surface-2)', color }}
      title={state}
    >
      <span aria-hidden>{human}</span>
      {state.replaceAll('_', ' ')}
    </span>
  )
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full" style={{ background: 'var(--gridline)' }}>
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: 'var(--series-1)' }}
      />
    </div>
  )
}

export function Empty({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-4 py-10 text-center">
      <p className="text-sm font-medium text-ink-secondary">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted">{hint}</p>}
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-6 text-xs text-muted">
      <span
        className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-hairline"
        style={{ borderTopColor: 'var(--accent)' }}
      />
      {label}
    </div>
  )
}

export function Code({ children, className = '' }: { children: string; className?: string }) {
  return (
    <pre
      className={`overflow-x-auto rounded-md border border-hairline p-3 font-mono text-[11px] leading-relaxed text-ink-secondary ${className}`}
      style={{ background: 'var(--surface-2)' }}
    >
      {children}
    </pre>
  )
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-1.5 text-xs">
      {items.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted">{key}</dt>
          <dd className="text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Banner({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warning' | 'critical'
  children: ReactNode
}) {
  const style = {
    info: { bg: 'rgba(42,120,214,0.10)', fg: 'var(--series-1)', icon: 'ℹ' },
    warning: { bg: 'rgba(250,178,25,0.14)', fg: 'var(--status-warning)', icon: '⚠' },
    critical: { bg: 'rgba(208,59,59,0.12)', fg: 'var(--status-critical)', icon: '✕' },
  }[tone]
  return (
    <div
      className="flex items-start gap-2 rounded-md px-3 py-2 text-xs"
      style={{ background: style.bg, color: 'var(--text-primary)' }}
    >
      <span aria-hidden style={{ color: style.fg }}>
        {style.icon}
      </span>
      <div className="flex-1">{children}</div>
    </div>
  )
}
