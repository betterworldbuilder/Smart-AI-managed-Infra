import { useCallback, useEffect, useRef, useState } from 'react'

/** Fetch on mount, poll on an interval, and expose a manual refresh. */
export function usePoll<T>(
  loader: () => Promise<T>,
  intervalMs = 0,
  deps: unknown[] = [],
): { data: T | null; error: string | null; loading: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const result = await loaderRef.current()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    if (intervalMs > 0) {
      const handle = setInterval(run, intervalMs)
      return () => {
        cancelled = true
        clearInterval(handle)
      }
    }
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps])

  const refresh = useCallback(() => setTick((value) => value + 1), [])
  return { data, error, loading, refresh }
}

export type PlatformEvent = {
  seq: number
  topic: string
  at: string
  data: Record<string, any>
}

/**
 * Live platform events over Server-Sent Events.
 *
 * `topics` filters what is kept; `onEvent` fires for every matching event so a
 * page can refresh exactly the data that changed.
 */
export function useEvents(
  topics: string[] = [],
  onEvent?: (event: PlatformEvent) => void,
): PlatformEvent[] {
  const [events, setEvents] = useState<PlatformEvent[]>([])
  const handler = useRef(onEvent)
  handler.current = onEvent
  const filter = topics.join(',')

  useEffect(() => {
    const wanted = filter ? filter.split(',') : []
    const source = new EventSource('/api/events/stream')
    const listener = (raw: MessageEvent) => {
      try {
        const event = JSON.parse(raw.data) as PlatformEvent
        if (wanted.length && !wanted.some((topic) => event.topic.startsWith(topic))) return
        setEvents((previous) => [...previous.slice(-99), event])
        handler.current?.(event)
      } catch {
        /* keep-alive comments and malformed frames are ignored */
      }
    }
    source.onmessage = listener
    // Named events (topic as the SSE event name) need explicit listeners.
    const named = [
      'deployment.created',
      'deployment.approved',
      'deployment.planned',
      'deployment.deploying',
      'deployment.progress',
      'deployment.running',
      'deployment.failed',
      'deployment.rejected',
      'deployment.modified',
      'deployment.rolling_back',
      'deployment.rolled_back',
      'deployment.event',
      'metrics',
      'audit',
      'advisor',
      'alert',
    ]
    named.forEach((topic) => source.addEventListener(topic, listener as EventListener))
    return () => source.close()
  }, [filter])

  return events
}

/** Light/dark preference, persisted per browser. */
export function useTheme(): [string, (value: string) => void] {
  const [theme, setThemeState] = useState<string>(() => {
    try {
      return localStorage.getItem('aiinfra.theme') ?? 'system'
    } catch {
      return 'system'
    }
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('aiinfra.theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  return [theme, setThemeState]
}
