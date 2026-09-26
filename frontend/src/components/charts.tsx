import { useMemo, useRef, useState } from 'react'
import type { MetricSeries } from '../api/types'

/*
 * Charts follow one rule set:
 *   - categorical identity uses the three validated series slots, in fixed
 *     order, never cycled;
 *   - magnitude uses a single sequential hue;
 *   - every series is direct-labelled with its current value, so identity and
 *     magnitude never depend on colour alone;
 *   - line charts ship a crosshair + tooltip.
 */

export const SERIES_COLORS = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)'] as const

type Point = { x: number; y: number }

function toPoints(series: MetricSeries | undefined, count: number): number[] {
  const values = (series?.points ?? []).map((point) => point.value)
  if (values.length >= count) return values.slice(-count)
  return [...Array(count - values.length).fill(values[0] ?? 0), ...values]
}

function path(points: Point[]): string {
  return points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`).join(' ')
}

/** Small inline trend, no axes -- always paired with a printed value. */
export function Sparkline({
  values,
  color = 'var(--series-1)',
  width = 120,
  height = 28,
}: {
  values: number[]
  color?: string
  width?: number
  height?: number
}) {
  if (!values.length) return <svg width={width} height={height} aria-hidden />
  const max = Math.max(...values, 1)
  const min = Math.min(...values, 0)
  const span = max - min || 1
  const step = values.length > 1 ? width / (values.length - 1) : width
  const points = values.map((value, index) => ({
    x: index * step,
    y: height - 2 - ((value - min) / span) * (height - 4),
  }))
  return (
    <svg width={width} height={height} role="img" aria-hidden focusable="false">
      <path d={path(points)} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" />
    </svg>
  )
}

export type SeriesSpec = { key: string; label: string; unit?: string }

/**
 * Multi-series utilisation over time with a crosshair tooltip.
 * One y-axis, always; two measures of different scale belong in two charts.
 */
export function TimeSeriesChart({
  series,
  specs,
  height = 190,
  maxY = 100,
}: {
  series: Record<string, MetricSeries>
  specs: SeriesSpec[]
  height?: number
  maxY?: number
}) {
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const width = 640
  const pad = { top: 12, right: 12, bottom: 20, left: 32 }
  const plotW = width - pad.left - pad.right
  const plotH = height - pad.top - pad.bottom
  const count = 30

  const data = useMemo(
    () => specs.map((spec) => ({ spec, values: toPoints(series[spec.key], count) })),
    [series, specs],
  )
  const step = plotW / (count - 1)

  const scaleY = (value: number) => pad.top + plotH - (Math.min(value, maxY) / maxY) * plotH

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const x = ((event.clientX - rect.left) / rect.width) * width - pad.left
    const index = Math.round(x / step)
    setHover(index >= 0 && index < count ? index : null)
  }

  const gridValues = [0, 25, 50, 75, 100].filter((value) => value <= maxY)

  return (
    <div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ maxHeight: height }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`Utilisation over time: ${specs.map((s) => s.label).join(', ')}`}
      >
        {gridValues.map((value) => (
          <g key={value}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={scaleY(value)}
              y2={scaleY(value)}
              stroke="var(--gridline)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 6}
              y={scaleY(value) + 3}
              textAnchor="end"
              fontSize={9}
              fill="var(--text-muted)"
              className="tabular"
            >
              {value}
            </text>
          </g>
        ))}

        {data.map(({ spec, values }, index) => {
          const points = values.map((value, i) => ({ x: pad.left + i * step, y: scaleY(value) }))
          return (
            <path
              key={spec.key}
              d={path(points)}
              fill="none"
              stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )
        })}

        {hover !== null && (
          <g>
            <line
              x1={pad.left + hover * step}
              x2={pad.left + hover * step}
              y1={pad.top}
              y2={pad.top + plotH}
              stroke="var(--text-muted)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            {data.map(({ spec, values }, index) => (
              <circle
                key={spec.key}
                cx={pad.left + hover * step}
                cy={scaleY(values[hover] ?? 0)}
                r={4}
                fill={SERIES_COLORS[index % SERIES_COLORS.length]}
                stroke="var(--surface-1)"
                strokeWidth={2}
              />
            ))}
          </g>
        )}
      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {data.map(({ spec, values }, index) => {
          const current = hover !== null ? values[hover] : values[values.length - 1]
          return (
            <span key={spec.key} className="flex items-center gap-1.5 text-xs">
              <span
                aria-hidden
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }}
              />
              <span className="text-ink-secondary">{spec.label}</span>
              <span className="tabular font-medium text-ink">
                {(current ?? 0).toFixed(0)}
                {spec.unit ?? '%'}
              </span>
            </span>
          )
        })}
        {hover !== null && <span className="text-[11px] text-muted">at cursor</span>}
      </div>
    </div>
  )
}

/** Ranked horizontal magnitude bars with per-mark hover and direct labels. */
export function BarList({
  items,
  unit = '%',
  max = 100,
}: {
  items: { label: string; value: number; sublabel?: string; tone?: string }[]
  unit?: string
  max?: number
}) {
  if (!items.length) return <p className="text-xs text-muted">Nothing to show.</p>
  return (
    <ul className="space-y-2.5">
      {items.map((item) => {
        const pct = Math.min(100, (item.value / (max || 1)) * 100)
        return (
          <li key={item.label} title={`${item.label}: ${item.value}${unit}`}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-xs text-ink">{item.label}</span>
              <span className="tabular shrink-0 text-xs text-ink-secondary">
                {item.value.toFixed(0)}
                {unit}
                {item.sublabel && <span className="ml-2 text-muted">{item.sublabel}</span>}
              </span>
            </div>
            <div
              className="mt-1 h-2 w-full overflow-hidden rounded-full"
              style={{ background: 'var(--gridline)' }}
            >
              <div
                className="h-full rounded-full transition-[width] duration-500"
                style={{ width: `${pct}%`, background: item.tone ?? 'var(--seq-450)' }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
