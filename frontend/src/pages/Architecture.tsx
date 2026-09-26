import { useState } from 'react'
import { api } from '../api/client'
import type { Deployment } from '../api/types'
import TopologyFlow from '../components/TopologyFlow'
import { Card, StatusBadge } from '../components/ui'
import { usePoll } from '../hooks'
import { useCapabilities } from '../state'

export default function Architecture() {
  const capabilities = useCapabilities()
  const { data: deployments } = usePoll<Deployment[]>(() => api.deployments(), 5000)
  const [selected, setSelected] = useState<string>('')
  const { data: lifecycle } = usePoll<Record<string, any>[]>(() => api.lifecycle(), 0)

  const deployable = (deployments ?? []).filter((d) =>
    ['PLANNED', 'WAITING_FOR_FINAL_APPROVAL', 'DEPLOYING', 'RUNNING', 'FAILED'].includes(d.state),
  )

  return (
    <div className="space-y-4">
      <Card
        title="Platform architecture"
        subtitle="Highlight the path a deployment took through the stack"
        actions={
          <select
            className="input w-56 py-1 text-xs"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            aria-label="Highlight a deployment"
          >
            <option value="">No deployment highlighted</option>
            {deployable.map((deployment) => (
              <option key={deployment.id} value={deployment.id}>
                {deployment.name} ({deployment.state})
              </option>
            ))}
          </select>
        }
        bodyClassName="p-2"
      >
        <TopologyFlow deploymentId={selected || undefined} height={560} />
        <p className="px-2 py-2 text-[11px] text-muted">
          Dashed borders mark simulated components. Solid borders are real. The AI never chooses a
          node: the final hop is always <strong>kube-scheduler</strong> or{' '}
          <strong>nova-scheduler</strong>.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Separation of responsibilities">
          <ol className="space-y-2 text-xs">
            {[
              ['AI Copilot', 'understands the request and explains the options', capabilities?.copilot],
              ['AI Governor', 'scores runtimes against capacity and policy', capabilities?.governor],
              ['Human', 'decides — two mandatory gates', 'real'],
              ['openCenter', 'generates config and deploys deterministically', capabilities?.opencenter],
              ['Native scheduler', 'places the workload on a physical host', capabilities?.kubernetes],
              ['AI Copilot again', 'monitors and proposes improvements', capabilities?.copilot],
            ].map(([role, description, reality], index) => (
              <li key={String(role)} className="flex items-start gap-2.5">
                <span
                  className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white"
                  style={{ background: 'var(--accent)' }}
                  aria-hidden
                >
                  {index + 1}
                </span>
                <div className="flex-1">
                  <span className="font-medium text-ink">{role}</span>{' '}
                  <span className="text-ink-secondary">— {description}</span>
                </div>
                {reality && <StatusBadge value={String(reality)} />}
              </li>
            ))}
          </ol>
        </Card>

        <Card title="Deployment lifecycle" subtitle="Edges marked ⏸ require a human">
          <ul className="space-y-1 text-xs">
            {(lifecycle ?? []).map((edge) => (
              <li key={`${edge.from}-${edge.to}`} className="flex items-center gap-2">
                <span
                  aria-hidden
                  style={{ color: edge.gate ? 'var(--status-warning)' : 'var(--text-muted)' }}
                >
                  {edge.gate ? '⏸' : '→'}
                </span>
                <span className="font-mono text-[11px] text-ink">
                  {edge.from} → {edge.to}
                </span>
                {edge.requires_human && (
                  <span className="chip border-transparent" style={{ color: 'var(--status-warning)' }}>
                    human
                  </span>
                )}
                <span className="truncate text-muted">{edge.description}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  )
}
