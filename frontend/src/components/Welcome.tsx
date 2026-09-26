import { useNavigate } from 'react-router-dom'
import { useCapabilities, useFirstRun } from '../state'

const STEPS = [
  'You describe the workload in plain language',
  'The Copilot asks only what changes the answer',
  'The Governor scores every runtime against live capacity and policy',
  'You approve an option — the AI cannot',
  'openCenter generates the config and shows you the plan',
  'You authorise execution — the second gate',
  'The platform scheduler places it; monitoring and advice follow',
]

export default function Welcome() {
  const [open, dismiss] = useFirstRun()
  const capabilities = useCapabilities()
  const navigate = useNavigate()

  if (!open) return null

  const go = (path: string) => {
    dismiss()
    navigate(path)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="welcome-title"
    >
      <div className="card w-full max-w-2xl">
        <div className="card-pad">
          <h2 id="welcome-title" className="text-lg font-semibold">
            Welcome to the GPU Native Infra POC
          </h2>
          <p className="mt-1 text-sm text-ink-secondary">
            An AI Copilot proposes infrastructure, a human approves it, and openCenter deploys it.
            You are running in <strong>{capabilities?.mode_label ?? 'simulation'}</strong>
            {capabilities?.mode === 'simulation'
              ? ' — no GPU, OpenStack, Kubernetes or Ceph is required.'
              : ` — real Kubernetes cluster ${capabilities?.kubernetes_cluster}.`}
          </p>

          <ol className="mt-4 space-y-1.5">
            {STEPS.map((step, index) => (
              <li key={step} className="flex gap-2.5 text-sm text-ink-secondary">
                <span
                  className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white"
                  style={{ background: index === 3 || index === 5 ? 'var(--status-warning)' : 'var(--accent)' }}
                  aria-hidden
                >
                  {index + 1}
                </span>
                <span>
                  {step}
                  {(index === 3 || index === 5) && (
                    <strong className="ml-1 text-warning">(human gate)</strong>
                  )}
                </span>
              </li>
            ))}
          </ol>

          <div className="mt-5 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => go('/copilot?demo=llama')}
            >
              Start guided demo
            </button>
            <button type="button" className="btn" onClick={() => go('/')}>
              Explore dashboard
            </button>
            <button type="button" className="btn" onClick={() => go('/copilot')}>
              Ask the AI Copilot
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
