import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Recommendation, RecommendationOption } from '../api/types'
import OptionCard from '../components/OptionCard'
import { Banner, Card, Empty, Spinner } from '../components/ui'
import { relativeTime } from '../format'
import { usePoll } from '../hooks'

export default function Recommendations() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { data: list, loading } = usePoll<Recommendation[]>(() => api.recommendations(), 0, [id])
  const { data: single } = usePoll<Recommendation | null>(
    () => (id ? api.recommendation(id) : Promise.resolve(null)),
    0,
    [id],
  )

  const selected = single ?? (list ?? [])[0] ?? null

  const choose = async (option: RecommendationOption) => {
    if (!selected) return
    setBusy(true)
    try {
      const deployment = await api.createDeployment(selected.id, option.option)
      navigate(`/approval/${deployment.id}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setBusy(false)
    }
  }

  if (loading) return <Spinner label="Loading recommendations" />
  if (!selected) {
    return (
      <Card>
        <Empty
          title="No recommendations yet"
          hint="Ask the Copilot for a workload, or run a demo scenario."
        />
      </Card>
    )
  }

  const intent = selected.intent

  return (
    <div className="space-y-4">
      {error && <Banner tone="critical">{error}</Banner>}

      <Card
        title="Requirement"
        subtitle={`${relativeTime(selected.created_at)} · policy engine: ${selected.policy_engine}`}
        actions={
          (list ?? []).length > 1 && (
            <select
              className="input w-56 py-1 text-xs"
              value={selected.id}
              onChange={(event) => navigate(`/recommendations/${event.target.value}`)}
              aria-label="Choose a recommendation"
            >
              {(list ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.intent.name ?? item.intent.workload_type} · {relativeTime(item.created_at)}
                </option>
              ))}
            </select>
          )
        }
      >
        <p className="text-sm text-ink-secondary">{intent.description ?? selected.summary}</p>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs md:grid-cols-4">
          {[
            ['Workload', intent.workload_type],
            ['Environment', intent.environment],
            ['Data', intent.sensitive_data],
            ['Isolation', intent.workload_isolation],
            ['GPU required', intent.gpu_required ? 'yes' : 'no'],
            ['Concurrent users', intent.concurrent_users ?? '—'],
            ['High availability', intent.high_availability ? 'yes' : 'no'],
            ['Optimise for', intent.optimization_goal],
          ].map(([label, value]) => (
            <div key={String(label)}>
              <dt className="text-muted">{label}</dt>
              <dd className="text-ink">{String(value)}</dd>
            </div>
          ))}
        </dl>
        {selected.open_questions.length > 0 && (
          <div className="mt-3">
            <p className="label">Still open (safe defaults applied)</p>
            <ul className="mt-1 space-y-0.5">
              {selected.open_questions.map((question) => (
                <li key={question.field} className="text-xs text-muted">
                  · {question.question}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      <Banner tone="info">{selected.summary}</Banner>

      <div className="space-y-3">
        {selected.options.map((option) => (
          <OptionCard
            key={option.option}
            option={option}
            onSelect={busy ? undefined : choose}
            actionLabel="Select and review"
          />
        ))}
      </div>

      {selected.rejected_options.length > 0 && (
        <Card
          title="Rejected options"
          subtitle="Kept visible so the decision can be audited"
        >
          <div className="space-y-3">
            {selected.rejected_options.map((option) => (
              <OptionCard key={option.option} option={option} compact />
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
