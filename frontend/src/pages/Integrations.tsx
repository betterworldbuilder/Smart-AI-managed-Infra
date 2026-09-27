import { api } from '../api/client'
import type { Integration, RealityRow } from '../api/types'
import { Banner, Card, Code, Empty, StatusBadge } from '../components/ui'
import { usePoll } from '../hooks'
import { useCapabilities } from '../state'

export default function Integrations() {
  const capabilities = useCapabilities()
  const { data: integrations } = usePoll<Integration[]>(() => api.integrations(), 15000)
  const { data: reality } = usePoll<RealityRow[]>(() => api.realityMatrix(), 15000)
  const { data: health } = usePoll<Record<string, any>>(() => api.health(), 15000)

  return (
    <div className="space-y-4">
      <Banner tone="info">
        Switching an integration from simulated to real is an environment change, not a code
        change. Set the variable named on each row and restart the backend.
      </Banner>

      <Card title="Integrations" subtitle="One row per external system">
        {!integrations ? (
          <Empty title="Loading integrations" />
        ) : (
          <ul className="divide-y divide-hairline">
            {integrations.map((integration) => (
              <li key={integration.key} className="py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-ink">{integration.name}</span>
                  <StatusBadge value={integration.status} />
                  {integration.configure && (
                    <code className="ml-auto font-mono text-[11px] text-muted">
                      {integration.configure}
                    </code>
                  )}
                </div>
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-[11px] text-muted">details</summary>
                  <Code className="mt-1.5">{JSON.stringify(integration.detail, null, 2)}</Code>
                </details>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Reality matrix" subtitle="POC baseline versus this run">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th className="py-1.5 font-medium">Component</th>
                <th className="py-1.5 font-medium">POC</th>
                <th className="py-1.5 font-medium">Now</th>
              </tr>
            </thead>
            <tbody>
              {(reality ?? []).map((row) => (
                <tr key={row.component} className="border-b border-hairline/60 last:border-0">
                  <td className="py-1.5 text-ink">{row.component}</td>
                  <td className="py-1.5">
                    <StatusBadge value={row.poc} />
                  </td>
                  <td className="py-1.5">
                    <StatusBadge value={row.current} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Runtime configuration">
          {capabilities && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              {[
                ['INFRA_MODE', capabilities.mode],
                ['Deployment engine', capabilities.deployment_engine],
                ['openCenter', capabilities.opencenter],
                ['Kubernetes cluster', capabilities.kubernetes_cluster],
                ['Flux', capabilities.flux],
                ['Genestack', capabilities.genestack],
                ['Policy engine', capabilities.policy_engine],
                ['LLM provider', `${capabilities.llm_provider} ${capabilities.llm_model ?? ''}`],
                ['Auth', capabilities.auth_enabled ? 'enabled (single account)' : 'disabled (no login)'],
                ['Store', health?.store ?? '—'],
                [
                  'Redis',
                  health?.redis?.reachable
                    ? 'connected'
                    : health?.redis?.configured
                      ? 'configured but unreachable'
                      : 'not configured',
                ],
              ].map(([key, value]) => (
                <div key={String(key)} className="contents">
                  <dt className="text-muted">{key}</dt>
                  <dd className="text-ink">{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          <p className="mt-3 text-[11px] text-muted">
            GPU in the cluster: {capabilities?.kubernetes_gpu} — {capabilities?.kubernetes_gpu_reason}
          </p>
        </Card>
      </div>
    </div>
  )
}
