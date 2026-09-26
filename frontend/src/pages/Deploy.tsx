import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Recommendation, RecommendationOption, WorkloadIntent } from '../api/types'
import OptionCard from '../components/OptionCard'
import { Banner, Card, Spinner } from '../components/ui'
import { usePoll } from '../hooks'

const DEFAULT_INTENT: WorkloadIntent = {
  name: null,
  description: null,
  workload_type: 'llm-inference',
  environment: 'prod',
  operating_system: null,
  cpu_required: true,
  gpu_required: true,
  estimated_vcpu: null,
  estimated_ram_gb: null,
  gpu_count: null,
  gpu_memory_gb: null,
  preferred_gpu: null,
  storage_gb: null,
  high_availability: false,
  sensitive_data: 'internal',
  internet_access: false,
  network_exposure: 'internal',
  expected_users: null,
  concurrent_users: null,
  workload_isolation: 'medium',
  optimization_goal: 'balanced',
  latency_sensitive: false,
  container_compatible: null,
  replicas: null,
}

const STEPS = ['Workload', 'Sizing', 'Constraints', 'Review'] as const

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-[11px] text-muted">{hint}</span>}
    </label>
  )
}

export default function Deploy() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [intent, setIntent] = useState<WorkloadIntent>(DEFAULT_INTENT)
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data: profiles } = usePoll<Record<string, any>[]>(() => api.profiles(), 0)

  const set = <K extends keyof WorkloadIntent>(key: K, value: WorkloadIntent[K]) =>
    setIntent((current) => ({ ...current, [key]: value }))

  const number = (value: string) => (value === '' ? null : Number(value))

  const score = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.recommend(intent)
      setRecommendation(result)
      setStep(3)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  const choose = async (option: RecommendationOption) => {
    if (!recommendation) return
    setBusy(true)
    try {
      const deployment = await api.createDeployment(
        recommendation.id,
        option.option,
        intent.name ?? undefined,
      )
      navigate(`/approval/${deployment.id}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {STEPS.map((label, index) => (
          <button
            key={label}
            type="button"
            className="chip"
            style={
              index === step
                ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                : undefined
            }
            onClick={() => setStep(index)}
          >
            {index + 1}. {label}
          </button>
        ))}
        <span className="ml-auto text-[11px] text-muted">
          The wizard produces a requirement, never a deployment.
        </span>
      </div>

      {error && <Banner tone="critical">{error}</Banner>}

      {step === 0 && (
        <Card title="What are you deploying?">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Workload type">
              <select
                className="input mt-1"
                value={intent.workload_type}
                onChange={(event) => {
                  const key = event.target.value
                  const profile = (profiles ?? []).find((item) => item.key === key)
                  setIntent((current) => ({
                    ...current,
                    workload_type: key,
                    gpu_required: Boolean(profile?.gpu_required),
                    container_compatible: profile ? profile.container_compatible : null,
                    workload_isolation: profile?.default_isolation ?? current.workload_isolation,
                  }))
                }}
              >
                {(profiles ?? []).map((profile) => (
                  <option key={profile.key} value={profile.key}>
                    {profile.display}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Name" hint="DNS-safe; generated if empty">
              <input
                className="input mt-1"
                value={intent.name ?? ''}
                onChange={(event) => set('name', event.target.value || null)}
                placeholder="llama-internal"
              />
            </Field>
            <Field label="Environment">
              <select
                className="input mt-1"
                value={intent.environment}
                onChange={(event) => set('environment', event.target.value)}
              >
                {['dev', 'test', 'prod'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Operating system" hint="Only matters for VM workloads">
              <input
                className="input mt-1"
                value={intent.operating_system ?? ''}
                onChange={(event) => set('operating_system', event.target.value || null)}
                placeholder="ubuntu 22.04"
              />
            </Field>
            <Field label="Description">
              <textarea
                className="input mt-1"
                rows={3}
                value={intent.description ?? ''}
                onChange={(event) => set('description', event.target.value || null)}
                placeholder="Private Llama inference for the whole company"
              />
            </Field>
          </div>
        </Card>
      )}

      {step === 1 && (
        <Card title="Sizing" subtitle="Leave blank and the Governor derives it from demand">
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Expected users">
              <input
                className="input mt-1"
                type="number"
                min={1}
                value={intent.expected_users ?? ''}
                onChange={(event) => set('expected_users', number(event.target.value))}
              />
            </Field>
            <Field label="Concurrent users">
              <input
                className="input mt-1"
                type="number"
                min={1}
                value={intent.concurrent_users ?? ''}
                onChange={(event) => set('concurrent_users', number(event.target.value))}
              />
            </Field>
            <Field label="Storage (GB)">
              <input
                className="input mt-1"
                type="number"
                min={1}
                value={intent.storage_gb ?? ''}
                onChange={(event) => set('storage_gb', number(event.target.value))}
              />
            </Field>
            <Field label="vCPU per replica">
              <input
                className="input mt-1"
                type="number"
                min={1}
                value={intent.estimated_vcpu ?? ''}
                onChange={(event) => set('estimated_vcpu', number(event.target.value))}
              />
            </Field>
            <Field label="RAM per replica (GB)">
              <input
                className="input mt-1"
                type="number"
                min={1}
                value={intent.estimated_ram_gb ?? ''}
                onChange={(event) => set('estimated_ram_gb', number(event.target.value))}
              />
            </Field>
            <Field label="Replicas">
              <input
                className="input mt-1"
                type="number"
                min={1}
                value={intent.replicas ?? ''}
                onChange={(event) => set('replicas', number(event.target.value))}
              />
            </Field>
            <Field label="GPU required">
              <select
                className="input mt-1"
                value={intent.gpu_required ? 'yes' : 'no'}
                onChange={(event) => set('gpu_required', event.target.value === 'yes')}
              >
                <option value="yes">yes</option>
                <option value="no">no</option>
              </select>
            </Field>
            <Field label="GPU count">
              <input
                className="input mt-1"
                type="number"
                min={0}
                value={intent.gpu_count ?? ''}
                onChange={(event) => set('gpu_count', number(event.target.value))}
              />
            </Field>
            <Field label="GPU memory (GB)" hint="Minimum VRAM per card">
              <input
                className="input mt-1"
                type="number"
                min={1}
                value={intent.gpu_memory_gb ?? ''}
                onChange={(event) => set('gpu_memory_gb', number(event.target.value))}
              />
            </Field>
          </div>
        </Card>
      )}

      {step === 2 && (
        <Card title="Constraints" subtitle="These drive the policy engine">
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="Data classification">
              <select
                className="input mt-1"
                value={intent.sensitive_data}
                onChange={(event) => set('sensitive_data', event.target.value)}
              >
                {['public', 'internal', 'confidential', 'restricted'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Isolation">
              <select
                className="input mt-1"
                value={intent.workload_isolation}
                onChange={(event) => set('workload_isolation', event.target.value)}
              >
                {['low', 'medium', 'high'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="Network exposure">
              <select
                className="input mt-1"
                value={intent.network_exposure}
                onChange={(event) => set('network_exposure', event.target.value)}
              >
                {['internal', 'organisation', 'public'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
            <Field label="High availability">
              <select
                className="input mt-1"
                value={intent.high_availability ? 'yes' : 'no'}
                onChange={(event) => set('high_availability', event.target.value === 'yes')}
              >
                <option value="no">no</option>
                <option value="yes">yes</option>
              </select>
            </Field>
            <Field label="Container compatible">
              <select
                className="input mt-1"
                value={
                  intent.container_compatible === null
                    ? 'unknown'
                    : intent.container_compatible
                      ? 'yes'
                      : 'no'
                }
                onChange={(event) =>
                  set(
                    'container_compatible',
                    event.target.value === 'unknown' ? null : event.target.value === 'yes',
                  )
                }
              >
                <option value="unknown">let the Copilot decide</option>
                <option value="yes">yes</option>
                <option value="no">no — needs a full OS</option>
              </select>
            </Field>
            <Field label="Optimise for">
              <select
                className="input mt-1"
                value={intent.optimization_goal}
                onChange={(event) => set('optimization_goal', event.target.value)}
              >
                {['cost', 'balanced', 'performance'].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
          </div>
        </Card>
      )}

      {step === 3 && (
        <div className="space-y-3">
          {busy && <Spinner label="Scoring runtimes" />}
          {recommendation && (
            <>
              <Banner tone="info">{recommendation.summary}</Banner>
              {recommendation.options.map((option) => (
                <OptionCard
                  key={option.option}
                  option={option}
                  onSelect={choose}
                  actionLabel="Select and review"
                />
              ))}
              {recommendation.rejected_options.length > 0 && (
                <details className="card card-pad">
                  <summary className="cursor-pointer text-xs text-ink-secondary">
                    {recommendation.rejected_options.length} rejected option(s)
                  </summary>
                  <div className="mt-3 space-y-3">
                    {recommendation.rejected_options.map((option) => (
                      <OptionCard key={option.option} option={option} compact />
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          className="btn"
          disabled={step === 0}
          onClick={() => setStep((value) => Math.max(0, value - 1))}
        >
          Back
        </button>
        {step < 2 && (
          <button type="button" className="btn btn-primary" onClick={() => setStep(step + 1)}>
            Next
          </button>
        )}
        {step === 2 && (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={score}>
            Score the options
          </button>
        )}
        {step === 3 && (
          <button type="button" className="btn" disabled={busy} onClick={score}>
            Re-score
          </button>
        )}
      </div>
    </div>
  )
}
