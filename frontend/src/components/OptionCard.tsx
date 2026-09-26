import type { RecommendationOption } from '../api/types'
import { planSummary } from '../format'

const BREAKDOWN_LABELS: [keyof RecommendationOption['breakdown'], string, boolean][] = [
  ['compatibility', 'Compatibility', false],
  ['capacity', 'Available capacity', false],
  ['performance', 'Performance', false],
  ['isolation', 'Isolation', false],
  ['reliability', 'Reliability', false],
  ['efficiency', 'Resource efficiency', false],
  ['cost_penalty', 'Cost penalty', true],
  ['scarcity_penalty', 'Scarcity penalty', true],
  ['policy_delta', 'Policy adjustment', false],
]

export default function OptionCard({
  option,
  selected,
  onSelect,
  actionLabel = 'Select this option',
  compact = false,
}: {
  option: RecommendationOption
  selected?: boolean
  onSelect?: (option: RecommendationOption) => void
  actionLabel?: string
  compact?: boolean
}) {
  const plan = option.resources
  const blocked = !option.viable

  return (
    <article
      className={`card overflow-hidden ${selected ? 'ring-2' : ''}`}
      style={selected ? { boxShadow: '0 0 0 2px var(--accent)' } : undefined}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-hairline px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">{option.title}</h3>
            {option.recommended && (
              <span
                className="chip border-transparent"
                style={{ background: 'rgba(12,163,12,0.12)', color: 'var(--status-good)' }}
              >
                ★ Recommended
              </span>
            )}
            {blocked && (
              <span
                className="chip border-transparent"
                style={{ background: 'rgba(208,59,59,0.12)', color: 'var(--status-critical)' }}
              >
                ✕ Not viable
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-ink-secondary">{planSummary(plan)}</p>
        </div>
        <div className="text-right">
          <div className="tabular text-2xl font-semibold leading-none text-ink">
            {option.score.toFixed(0)}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-muted">score / 100</div>
        </div>
      </header>

      <div className="grid gap-4 px-4 py-3 md:grid-cols-2">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted">Replicas</dt>
          <dd className="tabular text-ink">{plan.replicas}</dd>
          <dt className="text-muted">CPU / replica</dt>
          <dd className="tabular text-ink">{plan.vcpu_per_replica} vCPU</dd>
          <dt className="text-muted">RAM / replica</dt>
          <dd className="tabular text-ink">{plan.ram_gb_per_replica} GB</dd>
          {plan.gpu_per_replica > 0 && (
            <>
              <dt className="text-muted">GPU</dt>
              <dd className="tabular text-ink">
                {plan.gpu_per_replica} x {plan.gpu_model}
                {plan.gpu_memory_gb ? ` (≥${plan.gpu_memory_gb} GB VRAM)` : ''}
                {plan.gpu_allocation_type ? ` · ${plan.gpu_allocation_type}` : ''}
              </dd>
            </>
          )}
          <dt className="text-muted">Storage</dt>
          <dd className="tabular text-ink">
            {plan.storage_gb} GB {plan.storage_backend}
          </dd>
          {plan.flavor && (
            <>
              <dt className="text-muted">Flavor</dt>
              <dd className="text-ink">{plan.flavor}</dd>
            </>
          )}
          {plan.image && (
            <>
              <dt className="text-muted">Image</dt>
              <dd className="text-ink">{plan.image}</dd>
            </>
          )}
          {plan.database_engine && (
            <>
              <dt className="text-muted">Engine</dt>
              <dd className="text-ink">{plan.database_engine}</dd>
            </>
          )}
          <dt className="text-muted">Placement by</dt>
          <dd className="text-ink">{option.native_scheduler}</dd>
        </dl>

        <div>
          <h4 className="label">Why</h4>
          <ul className="mt-1 space-y-1">
            {option.reason.slice(0, compact ? 3 : 8).map((reason) => (
              <li key={reason} className="flex gap-1.5 text-xs text-ink-secondary">
                <span aria-hidden style={{ color: 'var(--status-good)' }}>
                  +
                </span>
                {reason}
              </li>
            ))}
          </ul>
          {option.risks.length > 0 && (
            <>
              <h4 className="label mt-3">Risks</h4>
              <ul className="mt-1 space-y-1">
                {option.risks.map((risk) => (
                  <li key={risk} className="flex gap-1.5 text-xs text-ink-secondary">
                    <span aria-hidden style={{ color: 'var(--status-warning)' }}>
                      !
                    </span>
                    {risk}
                  </li>
                ))}
              </ul>
            </>
          )}
          {blocked && (
            <>
              <h4 className="label mt-3">Blocked by policy or capacity</h4>
              <ul className="mt-1 space-y-1">
                {option.blocked_by.map((reason) => (
                  <li key={reason} className="flex gap-1.5 text-xs text-ink-secondary">
                    <span aria-hidden style={{ color: 'var(--status-critical)' }}>
                      ✕
                    </span>
                    {reason}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>

      {!compact && (
        <details className="border-t border-hairline px-4 py-2">
          <summary className="cursor-pointer text-xs text-ink-secondary">
            Score breakdown and policy checks
          </summary>
          <div className="mt-2 grid gap-4 md:grid-cols-2">
            <ul className="space-y-1">
              {BREAKDOWN_LABELS.map(([key, label, negative]) => {
                const value = option.breakdown[key]
                if (!value) return null
                return (
                  <li key={key} className="flex items-baseline justify-between gap-3 text-xs">
                    <span className="text-muted">{label}</span>
                    <span
                      className="tabular"
                      style={{
                        color: negative ? 'var(--status-serious)' : 'var(--text-primary)',
                      }}
                    >
                      {negative ? '-' : '+'}
                      {Math.abs(value).toFixed(1)}
                    </span>
                  </li>
                )
              })}
            </ul>
            <ul className="space-y-1">
              {option.policy_decisions.length === 0 && (
                <li className="text-xs text-muted">No policy rule fired for this option.</li>
              )}
              {option.policy_decisions.map((decision) => (
                <li key={`${decision.rule_id}-${decision.title}`} className="text-xs">
                  <span className="font-mono text-[10px] text-muted">{decision.rule_id}</span>{' '}
                  <span className="text-ink">{decision.title}</span>
                  <p className="text-muted">{decision.message}</p>
                </li>
              ))}
            </ul>
          </div>
        </details>
      )}

      {onSelect && (
        <footer className="flex items-center justify-between gap-3 border-t border-hairline px-4 py-2.5">
          <span className="text-[11px] text-muted">
            Selecting an option does not deploy anything.
          </span>
          <button
            type="button"
            className="btn btn-primary"
            disabled={blocked}
            onClick={() => onSelect(option)}
          >
            {actionLabel}
          </button>
        </footer>
      )}
    </article>
  )
}
