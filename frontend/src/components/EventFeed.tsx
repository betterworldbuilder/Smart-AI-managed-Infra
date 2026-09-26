import { useEvents } from '../hooks'
import { clock } from '../format'
import { Card, Empty } from './ui'

const TOPIC_TONE: Record<string, string> = {
  'deployment.failed': 'var(--status-critical)',
  'deployment.running': 'var(--status-good)',
  'deployment.approved': 'var(--status-good)',
  'deployment.deploying': 'var(--series-2)',
  alert: 'var(--status-serious)',
  advisor: 'var(--series-1)',
  audit: 'var(--text-muted)',
  metrics: 'var(--text-muted)',
}

/** Live platform activity over Server-Sent Events. */
export default function EventFeed({ limit = 12 }: { limit?: number }) {
  const events = useEvents(['deployment', 'alert', 'advisor', 'audit'])
  const recent = events.slice(-limit).reverse()

  return (
    <Card title="Live activity" subtitle="Server-Sent Events from the control plane">
      {recent.length === 0 ? (
        <Empty title="Nothing has happened yet" hint="Actions appear here as they occur." />
      ) : (
        <ul className="space-y-1.5">
          {recent.map((event) => (
            <li key={event.seq} className="flex gap-2 text-xs">
              <span className="tabular shrink-0 text-muted">{clock(event.at)}</span>
              <span
                aria-hidden
                style={{ color: TOPIC_TONE[event.topic] ?? 'var(--text-muted)' }}
              >
                ●
              </span>
              <span className="font-mono text-[11px] text-ink-secondary">{event.topic}</span>
              <span className="truncate text-ink">
                {String(
                  event.data?.message ??
                    event.data?.name ??
                    event.data?.title ??
                    event.data?.alertname ??
                    '',
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
