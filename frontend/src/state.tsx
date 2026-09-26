import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken, setToken } from './api/client'
import type { Capabilities } from './api/types'

type AuthState = {
  username: string | null
  authEnabled: boolean
  ready: boolean
  warning: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)
const CapabilitiesContext = createContext<Capabilities | null>(null)

export function AppProviders({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null)
  const [authEnabled, setAuthEnabled] = useState(true)
  const [warning, setWarning] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)

  useEffect(() => {
    let cancelled = false
    const boot = async () => {
      try {
        const caps = await api.capabilities()
        if (!cancelled) {
          setCapabilities(caps)
          setAuthEnabled(caps.auth_enabled)
        }
        if (!caps.auth_enabled) {
          if (!cancelled) setUsername('demo')
        } else if (getToken()) {
          try {
            const me = await api.me()
            if (!cancelled) {
              setUsername(me.username)
              setWarning(me.warning)
            }
          } catch {
            setToken(null)
          }
        }
      } catch {
        /* the backend is not up yet; the login screen will retry */
      } finally {
        if (!cancelled) setReady(true)
      }
    }
    void boot()
    return () => {
      cancelled = true
    }
  }, [])

  // Capabilities can change between POC and MVP restarts; refresh periodically
  // so the header can never claim the wrong mode for long.
  useEffect(() => {
    const handle = setInterval(() => {
      api.capabilities().then(setCapabilities).catch(() => undefined)
    }, 30_000)
    return () => clearInterval(handle)
  }, [])

  const login = useCallback(async (user: string, password: string) => {
    const result = await api.login(user, password)
    setToken(result.access_token)
    setUsername(result.username)
    setWarning(result.warning)
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setUsername(null)
  }, [])

  const auth = useMemo<AuthState>(
    () => ({ username, authEnabled, ready, warning, login, logout }),
    [username, authEnabled, ready, warning, login, logout],
  )

  return (
    <AuthContext.Provider value={auth}>
      <CapabilitiesContext.Provider value={capabilities}>{children}</CapabilitiesContext.Provider>
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AppProviders')
  return value
}

export function useCapabilities(): Capabilities | null {
  return useContext(CapabilitiesContext)
}

const SEEN_KEY = 'aiinfra.welcome.seen'

export function useFirstRun(): [boolean, () => void] {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    try {
      setOpen(localStorage.getItem(SEEN_KEY) !== 'true')
    } catch {
      setOpen(true)
    }
  }, [])
  const dismiss = useCallback(() => {
    try {
      localStorage.setItem(SEEN_KEY, 'true')
    } catch {
      /* ignore */
    }
    setOpen(false)
  }, [])
  return [open, dismiss]
}
