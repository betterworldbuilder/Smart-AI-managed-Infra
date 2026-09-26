import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ClusterMetrics, Deployment, GlobalInventory, RealityRow } from '../api/types'
import EventFeed from '../components/EventFeed'
import { BarList, TimeSeriesChart } from '../components/charts'
import { Card, Empty, Meter, Progress, StateBadge, StatTile, StatusBadge } from '../components/ui'
import { relativeTime } from '../format'
import { usePoll } from '../hooks'
import { useCapabilities } from '../state'

export default function Dashboard() {
  const capabilities = useCapabilities()
  const { data: metrics } = usePoll<ClusterMetrics>(() => api.metrics(), 5000)
  const { data: inventory } = usePoll<GlobalInventory>(() => api.inventory(), 10000)
  const { data: deployments } = usePoll<Deployment[]>(() => api.deployments(), 5000)
  const { data: reality } = usePoll<RealityRow[]>(() => api.realityMatrix(), 0)

  const kubernetes = inventory?.platforms['kubernetes']
  const openstack = inventory?.platforms['openstack']
  const active = (deployments ?? []).filter((d) => d.state === 'DEPLOYING')
  const waiting = (deployments ?? []).filter((d) => d.state.startsWith('WAITING'))

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label="Clusters"
          value={2}
          detail={`${kubernetes?.nodes.length ?? 0} k8s nodes · ${
            openstack?.nodes.length ?? 0
          } compute hosts`}
        />
        <StatTile
          label="vCPU available"
          value={Math.round(inventory?.cpu.available ?? 0)}
          unit={`/ ${Math.round(inventory?.cpu.total ?? 0)}`}
        />
        <StatTile
          label="RAM available"
          value={Math.round(inventory?.ram_gb.available ?? 0)}
          unit={`GB / ${Math.round(inventory?.ram_gb.total ?? 0)}`}
        />
        <StatTile
          label="GPUs free"
          value={Math.round(inventory?.gpu.available ?? 0)}
          unit={`/ ${Math.round(inventory?.gpu.total ?? 0)}`}
          tone={
            (inventory?.gpu.available ?? 0) === 0
              ? 'critical'
              : (inventory?.gpu.available ?? 0) <= 2
                ? 'warning'
                : 'good'
          }
        />
        <StatTile
          label="Ceph free"
          value={(inventory?.storage.available_tb ?? 0).toFixed(1)}
          unit={`TB / ${(inventory?.storage.total_tb ?? 0).toFixed(0)}`}
        />
        <StatTile
          label="Running workloads"
          value={metrics?.running_deployments ?? 0}
          detail={`${metrics?.failed_workloads ?? 0} failed`}
          tone={(metrics?.failed_workloads ?? 0) > 0 ? 'warning' : 'neutral'}
        />
      </div>

      {waiting.length > 0 && (
        <Card
          title="Waiting for you"
          subtitle="The AI has stopped at a human approval gate"
          className="border-l-4"
        >
          <ul className="space-y-2">
            {waiting.map((deployment) => (
              <li key={deployment.id} className="flex flex-wrap items-center gap-3">
                <StateBadge state={deployment.state} />
                <Link
                  to={`/approval/${deployment.id}`}
                  className="text-sm font-medium text-ink underline-offset-2 hover:underline"
                >
                  {deployment.name}
                </Link>
                <span className="text-xs text-muted">
                  {deployment.option.title} · {relativeTime(deployment.updated_at)}
                </span>
                <Link className="btn ml-auto px-2 py-1 text-xs" to={`/approval/${deployment.id}`}>
                  Review
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card
          title="Utilisation"
          subtitle="Cluster-wide, last 30 samples"
          actions={
            <span className="text-[11px] text-muted">
              {metrics ? `updated ${relativeTime(metrics.generated_at)}` : ''}
            </span>
          }
        >
          {metrics ? (
            <TimeSeriesChart
              series={metrics.series}
              specs={[
                { key: 'cpu', label: 'CPU' },
                { key: 'ram', label: 'RAM' },
                { key: 'gpu', label: 'GPU' },
              ]}
            />
          ) : (
            <Empty title="Waiting for metrics" />
          )}
        </Card>

        <Card title="Capacity">
          <div className="space-y-3">
            <Meter
              label="CPU"
              used={(inventory?.cpu.total ?? 0) - (inventory?.cpu.available ?? 0)}
              total={inventory?.cpu.total ?? 0}
              unit="vCPU"
            />
            <Meter
              label="Memory"
              used={(inventory?.ram_gb.total ?? 0) - (inventory?.ram_gb.available ?? 0)}
              total={inventory?.ram_gb.total ?? 0}
              unit="GB"
            />
            <Meter
              label="GPU"
              used={(inventory?.gpu.total ?? 0) - (inventory?.gpu.available ?? 0)}
              total={inventory?.gpu.total ?? 0}
              unit="cards"
            />
            <Meter
              label="Ceph"
              used={(inventory?.storage.total_tb ?? 0) - (inventory?.storage.available_tb ?? 0)}
              total={inventory?.storage.total_tb ?? 0}
              unit="TB"
              format={(value) => value.toFixed(1)}
            />
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="GPU utilisation by device"
          subtitle="Allocated cards only"
          actions={
            <Link className="btn px-2 py-1 text-xs" to="/gpus">
              GPU inventory
            </Link>
          }
        >
          <BarList
            items={(metrics?.gpus ?? [])
              .filter((gpu) => gpu.status === 'allocated')
              .map((gpu) => ({
                label: `${gpu.gpu_id} · ${gpu.model} @ ${gpu.host}`,
                value: gpu.utilization_pct,
                sublabel: gpu.temperature_c ? `${gpu.temperature_c.toFixed(0)}°C` : undefined,
              }))}
          />
          {(metrics?.gpus ?? []).filter((gpu) => gpu.status === 'allocated').length === 0 && (
            <Empty title="No GPU is allocated yet" hint="Deploy a GPU workload to see it here." />
          )}
        </Card>

        <Card
          title="Platform counts"
          subtitle="What is running across both platforms"
        >
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
            {[
              ['OpenStack VMs', metrics?.openstack_vm_count ?? 0],
              ['— of which GPU VMs', metrics?.openstack_gpu_vm_count ?? 0],
              ['Kubernetes pods', metrics?.kubernetes_pod_count ?? 0],
              ['— of which GPU pods', metrics?.kubernetes_gpu_pod_count ?? 0],
              ['Databases', metrics?.database_count ?? 0],
              ['Failed workloads', metrics?.failed_workloads ?? 0],
            ].map(([label, value]) => (
              <div key={String(label)} className="flex items-baseline justify-between gap-3">
                <dt className="text-muted">{label}</dt>
                <dd className="tabular font-medium text-ink">{value}</dd>
              </div>
            ))}
          </dl>

          {active.length > 0 && (
            <div className="mt-4 space-y-2 border-t border-hairline pt-3">
              <p className="label">Deploying now</p>
              {active.map((deployment) => (
                <div key={deployment.id}>
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <Link to={`/deployments/${deployment.id}`} className="text-ink hover:underline">
                      {deployment.name}
                    </Link>
                    <span className="tabular text-muted">{deployment.progress}%</span>
                  </div>
                  <div className="mt-1">
                    <Progress value={deployment.progress} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <EventFeed />

      <Card
        title="What is real right now"
        subtitle="POC baseline versus this run — never guess from the UI alone"
        actions={capabilities && <StatusBadge value={capabilities.mode_label} />}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[420px] text-xs">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th className="py-1.5 font-medium">Component</th>
                <th className="py-1.5 font-medium">POC</th>
                <th className="py-1.5 font-medium">This run</th>
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
        </div>
      </Card>
    </div>
  )
}
