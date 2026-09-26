import { NavLink, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useTheme } from '../hooks'
import { useAuth, useCapabilities } from '../state'
import { StatusBadge } from './ui'

const NAV = [
  { to: '/', label: 'Dashboard', icon: '▦', end: true },
  { to: '/copilot', label: 'AI Copilot', icon: '✦' },
  { to: '/deploy', label: 'Deploy', icon: '⌁' },
  { to: '/recommendations', label: 'Recommendations', icon: '☰' },
  { to: '/deployments', label: 'Deployments', icon: '⚑' },
  { to: '/infrastructure', label: 'Infrastructure', icon: '⛁' },
  { to: '/gpus', label: 'GPU Inventory', icon: '▤' },
  { to: '/observability', label: 'Observability', icon: '◷' },
  { to: '/architecture', label: 'Architecture', icon: '⬡' },
  { to: '/audit', label: 'Audit', icon: '✓' },
  { to: '/settings/integrations', label: 'Integrations', icon: '⚙' },
]

/**
 * The header must make the execution mode unmissable: an operator should never
 * be able to confuse a simulated datacenter with a real one.
 */
function ModeBadge() {
  const capabilities = useCapabilities()
  if (!capabilities) return null
  const mvp = capabilities.mode === 'mvp'
  return (
    <div
      className="flex items-center gap-2 rounded-md px-2.5 py-1"
      style={{
        background: mvp ? 'rgba(12,163,12,0.12)' : 'rgba(250,178,25,0.16)',
        color: mvp ? 'var(--status-good)' : 'var(--status-warning)',
      }}
      title={
        mvp
          ? `Real kind cluster: ${capabilities.kubernetes_cluster}`
          : 'Every piece of infrastructure below the Governor is simulated'
      }
    >
      <span aria-hidden>{mvp ? '◆' : '◐'}</span>
      <span className="text-xs font-semibold tracking-wide">{capabilities.mode_label}</span>
    </div>
  )
}

export default function Layout({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useTheme()
  const { username, logout, authEnabled } = useAuth()
  const capabilities = useCapabilities()
  const location = useLocation()

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-hairline bg-surface md:flex">
        <div className="flex items-center gap-2 border-b border-hairline px-4 py-3.5">
          <span
            className="flex h-7 w-7 items-center justify-center rounded-md text-sm font-bold text-white"
            style={{ background: 'var(--accent)' }}
            aria-hidden
          >
            AI
          </span>
          <div className="leading-tight">
            <div className="text-sm font-semibold">Infra Copilot</div>
            <div className="text-[10px] uppercase tracking-wide text-muted">GPU-native cloud</div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto p-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition ${
                  isActive
                    ? 'bg-raised font-medium text-ink'
                    : 'text-ink-secondary hover:bg-raised hover:text-ink'
                }`
              }
            >
              <span aria-hidden className="w-4 text-center text-muted">
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        {capabilities && (
          <div className="space-y-1.5 border-t border-hairline px-3 py-3 text-[11px]">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted">Kubernetes</span>
              <StatusBadge value={capabilities.kubernetes} />
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted">openCenter</span>
              <StatusBadge value={capabilities.opencenter} />
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted">GPU (cluster)</span>
              <StatusBadge value={capabilities.kubernetes_gpu} />
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted">AI provider</span>
              <span className="text-ink-secondary">{capabilities.llm_provider}</span>
            </div>
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-hairline bg-surface px-4 py-2.5">
          <div className="md:hidden">
            <select
              className="input py-1"
              value={location.pathname}
              onChange={(event) => {
                window.location.href = event.target.value
              }}
              aria-label="Navigate"
            >
              {NAV.map((item) => (
                <option key={item.to} value={item.to}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <ModeBadge />
          <div className="flex-1" />
          <button
            type="button"
            className="btn px-2 py-1 text-xs"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="Toggle light / dark"
          >
            {theme === 'dark' ? '☾' : '☀'}
          </button>
          {username && (
            <div className="flex items-center gap-2 text-xs text-ink-secondary">
              <span className="hidden sm:inline">{username}</span>
              {authEnabled && (
                <button type="button" className="btn px-2 py-1 text-xs" onClick={logout}>
                  Sign out
                </button>
              )}
            </div>
          )}
        </header>

        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4">{children}</main>

        <footer className="border-t border-hairline px-4 py-2 text-[11px] text-muted">
          The AI recommends; a human approves; openCenter deploys; the platform scheduler places.
          {capabilities?.mode === 'simulation' && ' Nothing on this screen is real infrastructure.'}
        </footer>
      </div>
    </div>
  )
}
