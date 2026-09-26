import { api } from '../api/client'
import { Card, Empty, Meter, StatusBadge } from '../components/ui'
import { usePoll } from '../hooks'

type NodeEntry = {
  name: string
  cpu: { total: number; available: number }
  ram_gb: { total: number; available: number }
  gpus: { id: string; model: string; status: string }[]
  status: string
  availability_zone: string | null
  labels: Record<string, string>
}

function NodeRow({ node }: { node: NodeEntry }) {
  return (
    <li className="rounded-md border border-hairline p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-medium text-ink">{node.name}</span>
        <StatusBadge value={node.status === 'ready' || node.status === 'up' ? 'connected' : node.status} />
        {node.availability_zone && (
          <span className="text-[11px] text-muted">{node.availability_zone}</span>
        )}
        {node.gpus.length > 0 && (
          <span className="chip" style={{ color: 'var(--series-1)' }}>
            {node.gpus.length} x {node.gpus[0].model}
          </span>
        )}
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <Meter
          label="CPU"
          used={node.cpu.total - node.cpu.available}
          total={node.cpu.total}
          unit="vCPU"
        />
        <Meter
          label="RAM"
          used={node.ram_gb.total - node.ram_gb.available}
          total={node.ram_gb.total}
          unit="GB"
        />
      </div>
      {node.gpus.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {node.gpus.map((gpu) => (
            <li key={gpu.id}>
              <span className="chip">
                <span
                  aria-hidden
                  style={{
                    color:
                      gpu.status === 'available'
                        ? 'var(--status-good)'
                        : 'var(--status-warning)',
                  }}
                >
                  ●
                </span>
                {gpu.id} · {gpu.model} · {gpu.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export default function Infrastructure() {
  const { data: tree } = usePoll<Record<string, any>>(() => api.inventoryTree(), 8000)
  const { data: genestack } = usePoll<Record<string, any>>(() => api.genestack(), 15000)
  const { data: openstack } = usePoll<Record<string, any>>(() => api.openstack(), 15000)

  if (!tree) return <Empty title="Loading infrastructure" />

  const ceph = tree.ceph ?? { pools: [], total_tb: 0, available_tb: 0 }

  return (
    <div className="space-y-4">
      <Card
        title="Kubernetes"
        subtitle={`${tree.kubernetes?.name ?? 'cluster'} · ${
          tree.kubernetes?.nodes?.length ?? 0
        } nodes`}
        actions={<StatusBadge value={tree.kubernetes?.available ? 'connected' : 'unavailable'} />}
      >
        <ul className="space-y-2">
          {(tree.kubernetes?.nodes ?? []).map((node: NodeEntry) => (
            <NodeRow key={node.name} node={node} />
          ))}
        </ul>
      </Card>

      <Card
        title="OpenStack (Genestack)"
        subtitle={`${tree.openstack?.name ?? 'region'} · ${
          tree.openstack?.hosts?.length ?? 0
        } compute hosts`}
        actions={
          <StatusBadge
            value={genestack?.health?.ready ? 'connected' : 'simulated'}
            title="Genestack control plane"
          />
        }
      >
        <ul className="space-y-2">
          {(tree.openstack?.hosts ?? []).map((node: NodeEntry) => (
            <NodeRow key={node.name} node={node} />
          ))}
        </ul>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="label">Control plane services</h3>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {(genestack?.availability?.services ?? []).map((service: any) => (
                <li key={service.name}>
                  <span className="chip">
                    <span
                      aria-hidden
                      style={{
                        color:
                          service.state === 'up'
                            ? 'var(--status-good)'
                            : 'var(--status-critical)',
                      }}
                    >
                      ●
                    </span>
                    {service.name}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="label">Instances</h3>
            <ul className="mt-1 space-y-1 text-xs">
              {(tree.openstack?.instances ?? []).map((instance: any) => (
                <li key={instance.name} className="flex justify-between gap-2">
                  <span className="text-ink">{instance.name}</span>
                  <span className="text-muted">
                    {instance.flavor} @ {instance.host} · {instance.status}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <details className="mt-4">
          <summary className="cursor-pointer text-xs text-ink-secondary">
            Placement resource providers and flavors
          </summary>
          <div className="mt-2 grid gap-4 md:grid-cols-2">
            <div>
              <h4 className="label">Placement</h4>
              <ul className="mt-1 space-y-1 text-[11px]">
                {(openstack?.resource_providers ?? []).map((provider: any) => (
                  <li key={provider.uuid}>
                    <span className="font-mono text-ink">{provider.name}</span>
                    <span className="ml-2 text-muted">
                      {Object.entries(provider.inventories)
                        .map(
                          ([key, value]: [string, any]) =>
                            `${key} ${value.used}/${value.total}`,
                        )
                        .join(' · ')}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="label">Flavors</h4>
              <ul className="mt-1 space-y-1 text-[11px]">
                {(openstack?.flavors ?? []).map((flavor: any) => (
                  <li key={flavor.name}>
                    <span className="font-mono text-ink">{flavor.name}</span>
                    <span className="ml-2 text-muted">
                      {flavor.vcpu} vCPU · {flavor.ram_gb} GB · {flavor.disk_gb} GB
                      {flavor.gpu ? ` · ${flavor.gpu} GPU` : ''}
                      {flavor.extra_specs?.['pci_passthrough:alias']
                        ? ` · alias ${flavor.extra_specs['pci_passthrough:alias']}`
                        : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </details>
      </Card>

      <Card title="Ceph" subtitle={`${ceph.available_tb} TB free of ${ceph.total_tb} TB`}>
        <div className="space-y-3">
          {(ceph.pools ?? []).map((pool: any) => (
            <Meter
              key={pool.name}
              label={`${pool.name} (${pool.backend}, ${pool.consumer})`}
              used={pool.total_tb - pool.available_tb}
              total={pool.total_tb}
              unit="TB"
              format={(value) => value.toFixed(1)}
            />
          ))}
        </div>
      </Card>
    </div>
  )
}
