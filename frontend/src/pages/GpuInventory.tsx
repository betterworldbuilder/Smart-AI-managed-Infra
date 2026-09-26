import { api } from '../api/client'
import type { GPUDevice } from '../api/types'
import { BarList } from '../components/charts'
import { Card, Empty, StatTile, StatusBadge } from '../components/ui'
import { usePoll } from '../hooks'
import { useCapabilities } from '../state'

export default function GpuInventory() {
  const capabilities = useCapabilities()
  const { data } = usePoll<Record<string, any>>(() => api.gpus(), 5000)
  const { data: host } = usePoll<Record<string, any>>(() => api.hostGpu(), 15000)

  const devices: GPUDevice[] = data?.devices ?? []
  const byModel: Record<string, { total: number; available: number }> = data?.by_model ?? {}

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="GPUs total" value={data?.total ?? 0} />
        <StatTile
          label="Available"
          value={data?.available ?? 0}
          tone={(data?.available ?? 0) === 0 ? 'critical' : 'good'}
        />
        <StatTile
          label="Models"
          value={Object.keys(byModel).length}
          detail={Object.keys(byModel).join(', ')}
        />
        <StatTile
          label="Cluster GPU scheduling"
          value={capabilities?.kubernetes_gpu ?? '—'}
          detail={capabilities?.kubernetes_gpu_reason}
          tone={capabilities?.kubernetes_gpu === 'real' ? 'good' : 'warning'}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card title="GPU devices" subtitle="Model, host, allocation and live telemetry">
          {devices.length === 0 ? (
            <Empty title="No GPUs in inventory" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-xs">
                <thead>
                  <tr className="border-b border-hairline text-left text-muted">
                    <th className="py-1.5 font-medium">GPU</th>
                    <th className="py-1.5 font-medium">Model</th>
                    <th className="py-1.5 font-medium">Host</th>
                    <th className="py-1.5 font-medium">Memory</th>
                    <th className="py-1.5 font-medium">Allocation</th>
                    <th className="py-1.5 font-medium">Workload</th>
                    <th className="py-1.5 text-right font-medium">Util</th>
                    <th className="py-1.5 text-right font-medium">Temp</th>
                    <th className="py-1.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((gpu) => (
                    <tr key={gpu.id} className="border-b border-hairline/60 last:border-0">
                      <td className="py-1.5 font-mono text-ink">{gpu.id}</td>
                      <td className="py-1.5 text-ink">{gpu.model}</td>
                      <td className="py-1.5 text-ink-secondary">
                        {gpu.host}
                        <span className="ml-1 text-muted">({gpu.platform})</span>
                      </td>
                      <td className="tabular py-1.5 text-ink-secondary">
                        {gpu.memory_used_gb.toFixed(0)} / {gpu.memory_gb} GB
                      </td>
                      <td className="py-1.5 text-ink-secondary">{gpu.allocation_type}</td>
                      <td className="py-1.5 text-ink-secondary">{gpu.workload ?? '—'}</td>
                      <td className="tabular py-1.5 text-right text-ink">
                        {gpu.utilization_pct.toFixed(0)}%
                      </td>
                      <td className="tabular py-1.5 text-right text-ink-secondary">
                        {gpu.temperature_c ? `${gpu.temperature_c.toFixed(0)}°C` : '—'}
                      </td>
                      <td className="py-1.5">
                        <StatusBadge
                          value={gpu.status === 'available' ? 'available' : gpu.status}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="space-y-4">
          <Card title="By model">
            <BarList
              items={Object.entries(byModel).map(([model, counts]) => ({
                label: model,
                value: counts.total - counts.available,
                sublabel: `of ${counts.total} allocated`,
              }))}
              unit=""
              max={Math.max(1, ...Object.values(byModel).map((counts) => counts.total))}
            />
          </Card>

          <Card
            title="Host GPU"
            subtitle="A GPU on the host is not the same as a GPU the cluster can schedule"
          >
            {host?.visible_to_host ? (
              <div className="space-y-2 text-xs">
                {(host.gpus as Record<string, any>[]).map((gpu) => (
                  <div key={gpu.index} className="rounded-md border border-hairline p-2">
                    <div className="font-medium text-ink">{gpu.model}</div>
                    <div className="text-muted">
                      {gpu.memory_total_gb} GB · driver {gpu.driver_version}
                    </div>
                  </div>
                ))}
                <div className="flex items-center justify-between pt-1">
                  <span className="text-muted">Visible to host</span>
                  <StatusBadge value="connected" />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted">Visible to the cluster</span>
                  <StatusBadge value={host.visible_to_cluster ? 'connected' : 'unavailable'} />
                </div>
              </div>
            ) : (
              <Empty
                title="No host GPU detected"
                hint={host?.reason ?? 'nvidia-smi is not available in this container.'}
              />
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
