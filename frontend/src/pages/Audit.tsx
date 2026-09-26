import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { AuditEvent } from '../api/types'
import { Card, Empty, StatusBadge } from '../components/ui'
import { clock, relativeTime } from '../format'
import { usePoll } from '../hooks'

const ACTOR_TONE: Record<string, string> = {
  human: 'var(--status-good)',
  ai: 'var(--series-1)',
  system: 'var(--text-muted)',
}

export default function Audit() {
  const [filter, setFilter] = useState('')
  const [actor, setActor] = useState('all')
  const { data } = usePoll<AuditEvent[]>(() => api.audit(), 5000)

  const events = useMemo(() => {
    const all = data ?? []
    return all.filter((event) => {
      if (actor !== 'all' && event.actor_kind !== actor) return false
      if (!filter) return true
      const haystack = `${event.action} ${event.message} ${event.user}`.toLowerCase()
      return haystack.includes(filter.toLowerCase())
    })
  }, [data, filter, actor])

  return (
    <Card
      title="Audit trail"
      subtitle="Every AI suggestion, policy verdict and human decision"
      actions={
        <div className="flex gap-2">
          <select
            className="input w-32 py-1 text-xs"
            value={actor}
            onChange={(event) => setActor(event.target.value)}
            aria-label="Filter by actor"
          >
            <option value="all">all actors</option>
            <option value="human">human</option>
            <option value="ai">ai</option>
            <option value="system">system</option>
          </select>
          <input
            className="input w-48 py-1 text-xs"
            placeholder="Filter…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter audit events"
          />
        </div>
      }
    >
      {events.length === 0 ? (
        <Empty title="No audit events match" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead>
              <tr className="border-b border-hairline text-left text-muted">
                <th className="py-1.5 font-medium">Time</th>
                <th className="py-1.5 font-medium">Actor</th>
                <th className="py-1.5 font-medium">Action</th>
                <th className="py-1.5 font-medium">Message</th>
                <th className="py-1.5 font-medium">Resources</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id} className="border-b border-hairline/60 align-top last:border-0">
                  <td className="tabular whitespace-nowrap py-1.5 text-muted" title={event.timestamp}>
                    {clock(event.timestamp)}
                    <div className="text-[10px]">{relativeTime(event.timestamp)}</div>
                  </td>
                  <td className="py-1.5">
                    <span
                      className="chip border-transparent"
                      style={{
                        background: 'var(--surface-2)',
                        color: ACTOR_TONE[event.actor_kind] ?? 'var(--text-secondary)',
                      }}
                    >
                      {event.actor_kind === 'human' ? '👤' : event.actor_kind === 'ai' ? '✦' : '⚙'}
                      {event.user}
                    </span>
                  </td>
                  <td className="py-1.5 font-mono text-[11px] text-ink">{event.action}</td>
                  <td className="py-1.5 text-ink-secondary">
                    {event.message}
                    {event.deployment && (
                      <Link
                        to={`/deployments/${event.deployment}`}
                        className="ml-2 text-[11px] text-muted underline-offset-2 hover:underline"
                      >
                        {event.deployment}
                      </Link>
                    )}
                  </td>
                  <td className="py-1.5">
                    {Object.keys(event.resources).length > 0 && (
                      <span className="text-[11px] text-muted">
                        {Object.entries(event.resources)
                          .map(([key, value]) => `${key}=${value}`)
                          .join(' · ')}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[11px] text-muted">
        <StatusBadge value="real" /> The audit trail is what makes “the AI cannot deploy on its
        own” an auditable claim rather than a promise.
      </p>
    </Card>
  )
}
