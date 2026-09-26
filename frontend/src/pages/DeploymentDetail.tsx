import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { AuditEvent, Deployment } from '../api/types'
import TopologyFlow from '../components/TopologyFlow'
import { Sparkline } from '../components/charts'
import {
  Banner,
  Card,
  Code,
  Empty,
  KeyValue,
  Meter,
  Progress,
  Spinner,
  StateBadge,
} from '../components/ui'
import { clock, relativeTime } from '../format'
import { usePoll } from '../hooks'

export default function DeploymentDetail() {
  const { id = '' } = useParams()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data: deployment, refresh } = usePoll<Deployment>(() => api.deployment(id), 3000, [id])
  const { data: metrics } = usePoll<Record<string, any>>(
    () => api.deploymentMetrics(id),
    5000,
    [id],
  )
  const { data: audit } = usePoll<AuditEvent[]>(() => api.deploymentAudit(id), 5000, [id])
  const { data: live } = usePoll<Record<string, any>>(() => api.deploymentStatus(id), 5000, [id])

  if (!deployment) return <Spinner label="Loading deployment" />

  const rollback = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.rollback(id, 'requested from the deployment page')
      refresh()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  const plan = deployment.option.resources
  const pods = (live?.live?.pods ?? []) as Record<string, any>[]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{deployment.name}</h1>
        <StateBadge state={deployment.state} />
        <div className="flex-1" />
        {deployment.state.startsWith('WAITING') && (
          <Link className="btn btn-primary" to={`/approval/${deployment.id}`}>
            Review and approve
          </Link>
        )}
        {(deployment.state === 'RUNNING' || deployment.state === 'FAILED') && (
          <button type="button" className="btn btn-danger" disabled={busy} onClick={rollback}>
            Roll back
          </button>
        )}
      </div>

      {error && <Banner tone="critical">{error}</Banner>}
      {deployment.failure_reason && (
        <Banner tone="critical">{deployment.failure_reason}</Banner>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card title="Runtime" subtitle={deployment.option.title}>
          <KeyValue
            items={[
              ['Runtime', deployment.runtime.replaceAll('_', ' ')],
              ['Replicas', String(plan.replicas)],
              ['CPU', `${plan.vcpu_per_replica} vCPU x ${plan.replicas}`],
              ['Memory', `${plan.ram_gb_per_replica} GB x ${plan.replicas}`],
              [
                'GPU',
                plan.gpu_per_replica
                  ? `${plan.gpu_per_replica} x ${plan.gpu_model} (${plan.gpu_allocation_type})`
                  : 'none',
              ],
              ['Storage', `${plan.storage_gb} GB ${plan.storage_backend}`],
              ['Exposure', plan.network_exposure],
              [
                'Placement',
                deployment.placement
                  ? `${deployment.placement.scheduler} → ${
                      deployment.placement.nodes.join(', ') || deployment.placement.node
                    }`
                  : 'not scheduled yet',
              ],
              [
                'GPU devices',
                deployment.allocated_gpu_ids.length
                  ? deployment.allocated_gpu_ids.join(', ')
                  : '—',
              ],
            ]}
          />
          {deployment.state === 'DEPLOYING' && (
            <div className="mt-3">
              <Progress value={deployment.progress} />
            </div>
          )}
        </Card>

        <Card title="Live metrics" subtitle="Simulated in POC mode, real in MVP mode">
          {metrics ? (
            <div className="space-y-3">
              <Meter
                label="CPU"
                used={metrics.cpu_utilization_pct ?? 0}
                total={100}
                unit="%"
                format={(value) => value.toFixed(0)}
              />
              <Meter
                label="Memory"
                used={metrics.ram_used_gb ?? 0}
                total={metrics.ram_total_gb ?? 1}
                unit="GB"
                format={(value) => value.toFixed(0)}
              />
              {metrics.gpu_utilization_pct !== null && metrics.gpu_utilization_pct !== undefined && (
                <Meter
                  label="GPU"
                  used={metrics.gpu_utilization_pct}
                  total={100}
                  unit="%"
                  format={(value) => value.toFixed(0)}
                />
              )}
              <Meter
                label="Storage"
                used={metrics.storage_used_gb ?? 0}
                total={metrics.storage_total_gb ?? 1}
                unit="GB"
                format={(value) => value.toFixed(0)}
              />
              <div className="flex items-center gap-2 pt-1">
                <Sparkline
                  values={(metrics.series?.gpu?.points ?? []).map((point: any) => point.value)}
                />
                <span className="text-xs text-muted">GPU trend</span>
              </div>
            </div>
          ) : (
            <Empty title="No metrics yet" />
          )}
        </Card>
      </div>

      {pods.length > 0 && (
        <Card title="Kubernetes pods" subtitle="Read live from the cluster API">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th className="py-1.5 font-medium">Pod</th>
                <th className="py-1.5 font-medium">Phase</th>
                <th className="py-1.5 font-medium">Node</th>
                <th className="py-1.5 font-medium">Restarts</th>
              </tr>
            </thead>
            <tbody>
              {pods.map((pod) => (
                <tr key={pod.name} className="border-b border-hairline/60 last:border-0">
                  <td className="py-1.5 font-mono text-ink">{pod.name}</td>
                  <td className="py-1.5">{pod.phase}</td>
                  <td className="py-1.5">{pod.node}</td>
                  <td className="tabular py-1.5">{pod.restarts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Pipeline events">
          <ul className="space-y-1.5">
            {deployment.events.map((event) => (
              <li key={event.id} className="flex gap-2 text-xs">
                <span className="tabular shrink-0 text-muted">{clock(event.at)}</span>
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
            {deployment.events.length === 0 && <Empty title="No events yet" />}
          </ul>
        </Card>

        <Card title="Lifecycle" subtitle="Who moved this deployment, and when">
          <ol className="space-y-2">
            {deployment.history.map((transition, index) => (
              <li key={index} className="flex gap-2 text-xs">
                <span className="tabular shrink-0 text-muted">{clock(transition.at)}</span>
                <span className="text-ink">
                  {transition.from_state ?? 'start'} → <strong>{transition.to_state}</strong>
                </span>
                <span
                  className="chip border-transparent"
                  style={{
                    background:
                      transition.actor.kind === 'human'
                        ? 'rgba(12,163,12,0.12)'
                        : 'var(--surface-2)',
                    color:
                      transition.actor.kind === 'human'
                        ? 'var(--status-good)'
                        : 'var(--text-secondary)',
                  }}
                >
                  {transition.actor.kind === 'human' ? '👤' : '⚙'} {transition.actor.name}
                </span>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <Card title="Deployment path" subtitle="Highlighted through the platform architecture">
        <TopologyFlow deploymentId={deployment.id} height={320} />
      </Card>

      {deployment.spec && (
        <Card title="DeploymentSpec">
          <Code>{JSON.stringify(deployment.spec, null, 2)}</Code>
        </Card>
      )}

      <Card title="Audit trail" subtitle={`${audit?.length ?? 0} events`}>
        <ul className="space-y-1">
          {(audit ?? []).map((event) => (
            <li key={event.id} className="flex flex-wrap gap-2 text-xs">
              <span className="tabular shrink-0 text-muted">{relativeTime(event.timestamp)}</span>
              <span className="font-mono text-[11px] text-ink-secondary">{event.action}</span>
              <span className="text-muted">
                {event.user} ({event.actor_kind})
              </span>
              <span className="text-ink">{event.message}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
