import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Deployment } from '../api/types'
import OptionCard from '../components/OptionCard'
import { Banner, Card, Code, Empty, Progress, Spinner, StateBadge } from '../components/ui'
import { useEvents, usePoll } from '../hooks'

/**
 * The human approval page. Two gates live here:
 *   WAITING_FOR_HUMAN            -> approve the option
 *   WAITING_FOR_FINAL_APPROVAL   -> authorise execution of the plan
 */
export default function Approval() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data: deployment, refresh } = usePoll<Deployment>(() => api.deployment(id), 2000, [id])
  const { data: spec } = usePoll<{ yaml: string }>(
    () => api.deploymentSpec(id).catch(() => ({ yaml: '' })),
    0,
    [id, deployment?.state],
  )

  // Live pipeline updates: the SSE stream tells us exactly when to re-read.
  useEvents(['deployment'], (event) => {
    if (event.data?.id === id) refresh()
  })

  if (!deployment) return <Spinner label="Loading deployment" />

  const act = async (action: 'approve' | 'apply' | 'reject') => {
    setBusy(true)
    setError(null)
    try {
      if (action === 'approve') await api.approve(id)
      if (action === 'apply') await api.applyDeployment(id)
      if (action === 'reject') await api.reject(id, 'cancelled from the approval page')
      refresh()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  const gate1 = deployment.state === 'WAITING_FOR_HUMAN'
  const gate2 = deployment.state === 'WAITING_FOR_FINAL_APPROVAL'
  const running = ['DEPLOYING', 'RUNNING', 'FAILED', 'ROLLING_BACK', 'ROLLED_BACK'].includes(
    deployment.state,
  )
  const plan = deployment.plan
  const errors = (plan?.issues ?? []).filter((issue) => issue.severity === 'error')
  const warnings = (plan?.issues ?? []).filter((issue) => issue.severity === 'warning')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{deployment.name}</h1>
        <StateBadge state={deployment.state} />
        <span className="text-xs text-muted">{deployment.runtime.replaceAll('_', ' ')}</span>
        <div className="flex-1" />
        <Link className="btn px-2 py-1 text-xs" to={`/deployments/${deployment.id}`}>
          Full detail
        </Link>
      </div>

      {error && <Banner tone="critical">{error}</Banner>}

      {gate1 && (
        <Banner tone="warning">
          <strong>Human approval gate 1 of 2.</strong> The Governor has ranked the options and
          stopped. Approving generates the DeploymentSpec and asks openCenter for a plan — it still
          does not deploy anything.
        </Banner>
      )}
      {gate2 && (
        <Banner tone="warning">
          <strong>Human approval gate 2 of 2.</strong> This is the last step before real changes.
          Review exactly what will be created below.
        </Banner>
      )}

      <OptionCard option={deployment.option} />

      {plan && (
        <Card
          title="What will be created"
          subtitle={`openCenter plan ${plan.id} · cluster ${plan.cluster_id} · ${
            (plan.gitops as any)?.engine ?? 'gitops'
          }`}
        >
          <ul className="space-y-1 font-mono text-xs">
            {plan.changes.map((change) => (
              <li key={`${change.kind}-${change.name}`} className="flex gap-2">
                <span
                  aria-hidden
                  style={{
                    color:
                      change.action === 'delete'
                        ? 'var(--status-critical)'
                        : 'var(--status-good)',
                  }}
                >
                  {change.action === 'delete' ? '-' : change.action === 'update' ? '~' : '+'}
                </span>
                <span className="text-ink">
                  {change.kind} <strong>{change.name}</strong>
                  {change.detail && <span className="text-muted"> ({change.detail})</span>}
                </span>
              </li>
            ))}
          </ul>

          {errors.length > 0 && (
            <div className="mt-3 space-y-1">
              {errors.map((issue) => (
                <Banner key={issue.code + issue.message} tone="critical">
                  <strong>{issue.code}</strong> — {issue.message}
                </Banner>
              ))}
            </div>
          )}
          {warnings.length > 0 && (
            <div className="mt-3 space-y-1">
              {warnings.map((issue) => (
                <Banner key={issue.code + issue.message} tone="warning">
                  <strong>{issue.code}</strong> — {issue.message}
                </Banner>
              ))}
            </div>
          )}
        </Card>
      )}

      {spec?.yaml && (
        <Card
          title="DeploymentSpec"
          subtitle="The vendor-neutral contract between the AI Governor and openCenter"
        >
          <Code>{spec.yaml}</Code>
        </Card>
      )}

      {plan && plan.artifacts.length > 0 && (
        <Card
          title="Generated configuration"
          subtitle={`${plan.artifacts.length} files destined for the GitOps repository`}
        >
          <div className="space-y-2">
            {plan.artifacts.map((artifact) => (
              <details key={artifact.path} className="rounded-md border border-hairline">
                <summary className="cursor-pointer px-3 py-2 text-xs">
                  <span className="font-mono text-ink">{artifact.path}</span>
                  {artifact.description && (
                    <span className="ml-2 text-muted">{artifact.description}</span>
                  )}
                </summary>
                <div className="px-3 pb-3">
                  <Code>{artifact.content}</Code>
                </div>
              </details>
            ))}
          </div>
        </Card>
      )}

      {running && (
        <Card title="Execution" subtitle={`progress ${deployment.progress}%`}>
          <Progress value={deployment.progress} />
          <ul className="mt-3 space-y-1.5">
            {deployment.events.map((event) => (
              <li key={event.id} className="flex gap-2 text-xs">
                <span
                  aria-hidden
                  style={{
                    color:
                      event.level === 'error'
                        ? 'var(--status-critical)'
                        : event.level === 'success'
                          ? 'var(--status-good)'
                          : 'var(--text-muted)',
                  }}
                >
                  {event.level === 'error' ? '✕' : event.level === 'success' ? '✓' : '·'}
                </span>
                <span className="text-ink-secondary">{event.message}</span>
              </li>
            ))}
          </ul>
          {deployment.placement && (
            <p className="mt-3 text-xs text-ink-secondary">
              Placed by <strong>{deployment.placement.scheduler}</strong> on{' '}
              <strong>{deployment.placement.nodes.join(', ') || deployment.placement.node}</strong>
              {deployment.placement.gpu_ids.length > 0 &&
                ` · GPUs ${deployment.placement.gpu_ids.join(', ')}`}
            </p>
          )}
          {deployment.state === 'RUNNING' && (
            <div className="mt-3 flex gap-2">
              <Link className="btn btn-primary" to={`/deployments/${deployment.id}`}>
                Open the running workload
              </Link>
            </div>
          )}
        </Card>
      )}

      {(gate1 || gate2) && (
        <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-hairline bg-surface p-3">
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || (gate2 && errors.length > 0)}
            onClick={() => act(gate1 ? 'approve' : 'apply')}
          >
            {gate1 ? 'Approve this option' : 'Approve deployment'}
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => navigate(`/deploy?deployment=${deployment.id}`)}
          >
            Edit
          </button>
          <button type="button" className="btn btn-danger" disabled={busy} onClick={() => act('reject')}>
            Cancel
          </button>
          <span className="text-[11px] text-muted">
            {gate2 && errors.length > 0
              ? 'Blocked: the plan has validation errors.'
              : 'Recorded in the audit trail against your username.'}
          </span>
        </div>
      )}

      {!gate1 && !gate2 && !running && (
        <Empty title={`This deployment is ${deployment.state.replaceAll('_', ' ').toLowerCase()}`} />
      )}
    </div>
  )
}
