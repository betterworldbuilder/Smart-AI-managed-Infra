import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Deployment } from '../api/types'
import { Card, Empty, Progress, StateBadge } from '../components/ui'
import { relativeTime } from '../format'
import { usePoll } from '../hooks'

const GROUPS: { label: string; states: string[] }[] = [
  { label: 'Requested', states: ['DRAFT', 'RECOMMENDED'] },
  { label: 'Waiting for approval', states: ['WAITING_FOR_HUMAN', 'WAITING_FOR_FINAL_APPROVAL'] },
  { label: 'Approved / planned', states: ['APPROVED', 'PLANNED'] },
  { label: 'Deploying', states: ['DEPLOYING', 'ROLLING_BACK'] },
  { label: 'Running', states: ['RUNNING'] },
  { label: 'Finished', states: ['FAILED', 'REJECTED', 'ROLLED_BACK'] },
]

export default function Deployments() {
  const { data: deployments } = usePoll<Deployment[]>(() => api.deployments(), 3000)
  const all = deployments ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Deployments</h1>
        <Link className="btn btn-primary" to="/copilot">
          New request
        </Link>
      </div>

      {all.length === 0 && (
        <Card>
          <Empty
            title="No deployments yet"
            hint="Ask the Copilot for something, approve an option, and it will appear here."
          />
        </Card>
      )}

      {GROUPS.map((group) => {
        const items = all.filter((deployment) => group.states.includes(deployment.state))
        if (!items.length) return null
        return (
          <Card key={group.label} title={group.label} subtitle={`${items.length} deployment(s)`}>
            <ul className="divide-y divide-hairline">
              {items.map((deployment) => (
                <li key={deployment.id} className="flex flex-wrap items-center gap-3 py-2.5">
                  <StateBadge state={deployment.state} />
                  <Link
                    to={
                      deployment.state.startsWith('WAITING')
                        ? `/approval/${deployment.id}`
                        : `/deployments/${deployment.id}`
                    }
                    className="text-sm font-medium text-ink hover:underline"
                  >
                    {deployment.name}
                  </Link>
                  <span className="text-xs text-muted">
                    {deployment.runtime.replaceAll('_', ' ').toLowerCase()}
                  </span>
                  {deployment.placement && (
                    <span className="text-xs text-muted">
                      on {deployment.placement.nodes.join(', ') || deployment.placement.node}
                    </span>
                  )}
                  <span className="ml-auto text-xs text-muted">
                    {relativeTime(deployment.updated_at)}
                  </span>
                  {deployment.state === 'DEPLOYING' && (
                    <div className="w-full">
                      <Progress value={deployment.progress} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        )
      })}
    </div>
  )
}
