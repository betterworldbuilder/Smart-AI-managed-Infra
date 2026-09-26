import { useEffect, useState } from 'react'
import { useAuth, useCapabilities } from '../state'
import { Banner } from '../components/ui'

export default function Login() {
  const { login } = useAuth()
  const capabilities = useCapabilities()
  const defaults = capabilities?.default_credentials ?? false
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Pre-fill the demo password only while the server still uses it. On a
  // public host install.sh replaces it, and the page stops advertising it.
  useEffect(() => {
    if (defaults) setPassword((current) => current || 'admin')
  }, [defaults])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-5 flex items-center gap-3">
          <span
            className="flex h-10 w-10 items-center justify-center rounded-lg text-base font-bold text-white"
            style={{ background: 'var(--accent)' }}
            aria-hidden
          >
            AI
          </span>
          <div>
            <h1 className="text-lg font-semibold">GPU Native Infra Copilot</h1>
            <p className="text-xs text-ink-secondary">
              {capabilities?.mode_label ?? 'Loading mode…'}
            </p>
          </div>
        </div>

        <form className="card card-pad space-y-3" onSubmit={submit}>
          <div>
            <label className="label" htmlFor="username">
              Username
            </label>
            <input
              id="username"
              className="input mt-1"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
            />
          </div>
          <div>
            <label className="label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              className="input mt-1"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </div>
          {error && <Banner tone="critical">{error}</Banner>}
          <button className="btn btn-primary w-full" type="submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
          {defaults ? (
            <Banner tone="warning">
              <strong>POC ONLY — DO NOT USE THESE CREDENTIALS IN PRODUCTION.</strong> The demo
              account is <code>admin / admin</code>. Set <code>AUTH_ENABLED=false</code> to skip
              sign-in entirely.
            </Banner>
          ) : (
            <Banner tone="info">
              This instance uses its own credentials. The operator finds them in the
              <code> .env</code> file on the server (<code>AUTH_USERNAME</code> /{' '}
              <code>AUTH_PASSWORD</code>), and <code>install.sh</code> prints them.
            </Banner>
          )}
        </form>
      </div>
    </div>
  )
}
