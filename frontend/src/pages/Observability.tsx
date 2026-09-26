import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { AdvisorRecommendation, ClusterMetrics, RemediationAlert } from '../api/types'
import { BarList, TimeSeriesChart } from '../components/charts'
import { Banner, Card, Empty, StatTile, StatusBadge } from '../components/ui'
import { relativeTime } from '../format'
import { usePoll } from '../hooks'

export default function Observability() {
  const [busy, setBusy] = useState(false)
  const { data: metrics } = usePoll<ClusterMetrics>(() => api.metrics(), 5000)
  const { data: workloads } = usePoll<Record<string, any>[]>(() => api.workloadMetrics(), 5000)
  const { data: advice, refresh: refreshAdvice } = usePoll<AdvisorRecommendation[]>(
    () => api.advice(),
    8000,
  )
  const { data: alerts, refresh: refreshAlerts } = usePoll<RemediationAlert[]>(
    () => api.alerts(),
    8000,
  )

  const run = async (action: () => Promise<unknown>, after: () => void) => {
    setBusy(true)
    try {
      await action()
      after()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatTile
          label="CPU"
          value={(metrics?.cpu_utilization_pct ?? 0).toFixed(0)}
          unit="%"
        />
        <StatTile label="RAM" value={(metrics?.ram_utilization_pct ?? 0).toFixed(0)} unit="%" />
        <StatTile
          label="GPU"
          value={(metrics?.gpu_utilization_pct ?? 0).toFixed(0)}
          unit="%"
          detail={`${metrics?.gpu_allocated ?? 0} of ${metrics?.gpu_total ?? 0} allocated`}
        />
        <StatTile
          label="GPU memory"
          value={(metrics?.gpu_memory_pct ?? 0).toFixed(0)}
          unit="%"
        />
        <StatTile
          label="Storage"
          value={(metrics?.storage_utilization_pct ?? 0).toFixed(0)}
          unit="%"
        />
      </div>

      <Card title="Cluster utilisation" subtitle="CPU, RAM and GPU on one scale">
        {metrics ? (
          <TimeSeriesChart
            series={metrics.series}
            specs={[
              { key: 'cpu', label: 'CPU' },
              { key: 'ram', label: 'RAM' },
              { key: 'gpu', label: 'GPU' },
            ]}
            height={220}
          />
        ) : (
          <Empty title="Waiting for metrics" />
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="GPU devices" subtitle="Utilisation and temperature">
          <BarList
            items={(metrics?.gpus ?? []).map((gpu) => ({
              label: `${gpu.gpu_id} · ${gpu.model}`,
              value: gpu.utilization_pct,
              sublabel: `${gpu.status}${
                gpu.temperature_c ? ` · ${gpu.temperature_c.toFixed(0)}°C` : ''
              }`,
              tone:
                gpu.status === 'unhealthy'
                  ? 'var(--status-critical)'
                  : gpu.utilization_pct > 85
                    ? 'var(--status-warning)'
                    : 'var(--seq-450)',
            }))}
          />
        </Card>

        <Card title="Workloads" subtitle="Per-deployment metrics">
          {(workloads ?? []).length === 0 ? (
            <Empty title="No running workloads" />
          ) : (
            <ul className="space-y-2">
              {(workloads ?? []).map((workload) => (
                <li key={workload.deployment_id} className="rounded-md border border-hairline p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      to={`/deployments/${workload.deployment_id}`}
                      className="text-xs font-medium text-ink hover:underline"
                    >
                      {workload.name}
                    </Link>
                    <StatusBadge value={workload.healthy ? 'healthy' : 'failed'} />
                    <span className="ml-auto text-[11px] text-muted">
                      {workload.runtime.replaceAll('_', ' ').toLowerCase()}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-secondary">
                    <span>CPU {workload.cpu_utilization_pct?.toFixed(0)}%</span>
                    <span>
                      RAM {workload.ram_used_gb?.toFixed(0)} / {workload.ram_total_gb} GB
                    </span>
                    {workload.gpu_utilization_pct !== null && (
                      <span>GPU {workload.gpu_utilization_pct?.toFixed(0)}%</span>
                    )}
                    <span>
                      Storage {workload.storage_used_gb?.toFixed(0)} /{' '}
                      {workload.storage_total_gb} GB
                    </span>
                    <span>{workload.requests_per_second?.toFixed(0)} req/s</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card
        title="Post-deployment advice"
        subtitle="The Copilot keeps watching — and keeps its hands off"
        actions={
          <button
            type="button"
            className="btn px-2 py-1 text-xs"
            disabled={busy}
            onClick={() => run(() => api.analyse(), refreshAdvice)}
          >
            Analyse now
          </button>
        }
      >
        {(advice ?? []).length === 0 ? (
          <Empty
            title="No recommendations"
            hint="Deploy something and let it run; the advisor looks for over- and under-provisioning."
          />
        ) : (
          <ul className="space-y-3">
            {(advice ?? []).map((item) => (
              <li key={item.id} className="rounded-md border border-hairline p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-ink">{item.title}</span>
                  <StatusBadge value={item.status === 'open' ? 'simulated' : item.status} />
                  <span className="ml-auto text-[11px] text-muted">
                    {relativeTime(item.created_at)}
                  </span>
                </div>
                <ul className="mt-1.5 space-y-0.5">
                  {item.observation.map((line) => (
                    <li key={line} className="text-xs text-ink-secondary">
                      · {line}
                    </li>
                  ))}
                </ul>
                <p className="mt-1.5 text-xs text-ink">{item.recommendation}</p>
                {item.estimated_savings && (
                  <p className="mt-1 text-[11px] text-muted">Saving: {item.estimated_savings}</p>
                )}
                {item.simulation && (
                  <Banner tone="info">
                    <div className="space-y-0.5">
                      {Object.entries(item.simulation).map(([key, value]) => (
                        <div key={key}>
                          <span className="text-muted">{key}:</span> {String(value)}
                        </div>
                      ))}
                    </div>
                  </Banner>
                )}
                {item.status === 'open' && (
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      className="btn px-2 py-1 text-xs"
                      disabled={busy}
                      onClick={() => run(() => api.simulateAdvice(item.id), refreshAdvice)}
                    >
                      Simulate
                    </button>
                    <button
                      type="button"
                      className="btn px-2 py-1 text-xs"
                      disabled={busy}
                      onClick={() => run(() => api.ignoreAdvice(item.id), refreshAdvice)}
                    >
                      Ignore
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Remediation"
        subtitle="Platform alerts routed to the Governor — options, never actions"
        actions={
          <button
            type="button"
            className="btn px-2 py-1 text-xs"
            disabled={busy}
            onClick={() => run(() => api.simulateGpuFailure(), refreshAlerts)}
          >
            Simulate a GPU failure
          </button>
        }
      >
        {(alerts ?? []).length === 0 ? (
          <Empty title="No alerts" />
        ) : (
          <ul className="space-y-3">
            {(alerts ?? []).map((alert) => (
              <li key={alert.id} className="rounded-md border border-hairline p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge value={alert.severity === 'critical' ? 'failed' : 'simulated'} />
                  <span className="text-sm font-medium text-ink">{alert.alertname}</span>
                  <span className="font-mono text-xs text-muted">{alert.resource}</span>
                  <span className="ml-auto text-[11px] text-muted">{relativeTime(alert.at)}</span>
                </div>
                <p className="mt-1 text-xs text-ink-secondary">{alert.summary}</p>
                <ul className="mt-2 space-y-1.5">
                  {alert.options.map((option) => (
                    <li key={option.key} className="flex flex-wrap items-start gap-2 text-xs">
                      <span className="font-semibold text-ink">{option.key}.</span>
                      <div className="flex-1">
                        <div className="text-ink">
                          {option.title}
                          {option.recommended && (
                            <span
                              className="ml-2 chip border-transparent"
                              style={{
                                background: 'rgba(12,163,12,0.12)',
                                color: 'var(--status-good)',
                              }}
                            >
                              ★ recommended
                            </span>
                          )}
                        </div>
                        <div className="text-muted">{option.impact}</div>
                      </div>
                      {alert.status === 'open' && (
                        <button
                          type="button"
                          className="btn px-2 py-1 text-xs"
                          disabled={busy}
                          onClick={() =>
                            run(() => api.decideAlert(alert.id, option.key), refreshAlerts)
                          }
                        >
                          Approve {option.key}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
                {alert.status !== 'open' && (
                  <p className="mt-2 text-[11px] text-muted">
                    Decision: <strong>{alert.decision}</strong> ({alert.status})
                  </p>
                )}
                {alert.status === 'open' && (
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      className="btn px-2 py-1 text-xs"
                      disabled={busy}
                      onClick={() => run(() => api.decideAlert(alert.id, 'INVESTIGATE'), refreshAlerts)}
                    >
                      Investigate
                    </button>
                    <button
                      type="button"
                      className="btn px-2 py-1 text-xs"
                      disabled={busy}
                      onClick={() => run(() => api.decideAlert(alert.id, 'IGNORE'), refreshAlerts)}
                    >
                      Ignore
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
