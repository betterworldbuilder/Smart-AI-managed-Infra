import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Conversation, Recommendation, RecommendationOption, Scenario } from '../api/types'
import OptionCard from '../components/OptionCard'
import { Banner, Card, Empty, Spinner, StatusBadge } from '../components/ui'
import { clock } from '../format'
import { usePoll } from '../hooks'
import { useCapabilities } from '../state'

const DEMO_PROMPT = 'Deploy a private Llama inference service for 100 employees.'

/** Renders the Copilot's light markdown (bold, bullets, italics) safely. */
function Message({ role, content, at }: { role: string; content: string; at: string }) {
  const mine = role === 'user'
  const lines = content.split('\n')
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${mine ? 'text-white' : 'text-ink'}`}
        style={{
          background: mine ? 'var(--accent)' : 'var(--surface-2)',
        }}
      >
        {lines.map((line, index) => {
          if (!line.trim()) return <div key={index} className="h-2" />
          const bullet = line.trimStart().startsWith('- ')
          const text = bullet ? line.trimStart().slice(2) : line
          const parts = text.split(/(\*\*[^*]+\*\*|_[^_]+_|`[^`]+`)/g)
          const rendered = parts.map((part, partIndex) => {
            if (part.startsWith('**') && part.endsWith('**'))
              return <strong key={partIndex}>{part.slice(2, -2)}</strong>
            if (part.startsWith('_') && part.endsWith('_'))
              return (
                <em key={partIndex} className={mine ? '' : 'text-ink-secondary'}>
                  {part.slice(1, -1)}
                </em>
              )
            if (part.startsWith('`') && part.endsWith('`'))
              return (
                <code key={partIndex} className="font-mono text-[12px]">
                  {part.slice(1, -1)}
                </code>
              )
            return <span key={partIndex}>{part}</span>
          })
          return bullet ? (
            <div key={index} className="flex gap-1.5">
              <span aria-hidden className="opacity-60">
                •
              </span>
              <span>{rendered}</span>
            </div>
          ) : (
            <div key={index}>{rendered}</div>
          )
        })}
        <div className={`mt-1 text-[10px] ${mine ? 'text-white/70' : 'text-muted'}`}>
          {clock(at)}
        </div>
      </div>
    </div>
  )
}

export default function Copilot() {
  const navigate = useNavigate()
  const capabilities = useCapabilities()
  const [params, setParams] = useSearchParams()
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const demoStarted = useRef(false)

  const { data: scenarios } = usePoll<Scenario[]>(() => api.scenarios(), 0)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversation?.messages.length, recommendation?.id])

  const send = useCallback(
    async (message: string) => {
      if (!message.trim() || busy) return
      setBusy(true)
      setError(null)
      // Optimistically show what the operator typed.
      setConversation((current) =>
        current
          ? {
              ...current,
              messages: [
                ...current.messages,
                {
                  id: `local-${Date.now()}`,
                  role: 'user',
                  content: message,
                  at: new Date().toISOString(),
                  meta: {},
                },
              ],
            }
          : current,
      )
      setDraft('')
      try {
        const result = await api.sendMessage(message, conversation?.id ?? null)
        setConversation(result.conversation)
        setRecommendation(result.recommendation)
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc))
      } finally {
        setBusy(false)
      }
    },
    [busy, conversation?.id],
  )

  const answer = useCallback(
    async (field: string, value: unknown) => {
      if (!conversation) return
      setBusy(true)
      setError(null)
      try {
        const result = await api.answer(conversation.id, { [field]: value })
        setConversation(result.conversation)
        setRecommendation(result.recommendation)
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc))
      } finally {
        setBusy(false)
      }
    },
    [conversation],
  )

  // Guided demo: /copilot?demo=llama
  useEffect(() => {
    if (params.get('demo') && !demoStarted.current) {
      demoStarted.current = true
      params.delete('demo')
      setParams(params, { replace: true })
      void send(DEMO_PROMPT)
    }
  }, [params, send, setParams])

  const select = async (option: RecommendationOption) => {
    if (!recommendation) return
    setBusy(true)
    try {
      const deployment = await api.createDeployment(recommendation.id, option.option)
      navigate(`/approval/${deployment.id}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setBusy(false)
    }
  }

  const questions = conversation?.pending_questions ?? []
  const intentEntries = useMemo(() => {
    const intent = conversation?.intent
    if (!intent) return []
    const interesting: [string, unknown][] = [
      ['workload', intent.workload_type],
      ['environment', intent.environment],
      ['GPU required', intent.gpu_required ? 'yes' : 'no'],
      ['expected users', intent.expected_users],
      ['concurrent users', intent.concurrent_users],
      ['data', intent.sensitive_data],
      ['isolation', intent.workload_isolation],
      ['high availability', intent.high_availability ? 'yes' : 'no'],
      ['storage', intent.storage_gb ? `${intent.storage_gb} GB` : null],
      ['OS', intent.operating_system],
    ]
    return interesting.filter(([, value]) => value !== null && value !== undefined && value !== '')
  }, [conversation?.intent])

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-4">
        <Card
          title="AI Infra Copilot"
          subtitle={
            capabilities
              ? `Provider: ${capabilities.llm_provider}${
                  capabilities.llm_model ? ` (${capabilities.llm_model})` : ''
                } · the Copilot only ever sees sanitised capacity facts`
              : undefined
          }
          actions={
            <button
              type="button"
              className="btn px-2 py-1 text-xs"
              onClick={() => {
                setConversation(null)
                setRecommendation(null)
                setError(null)
              }}
            >
              New request
            </button>
          }
          bodyClassName="flex h-[52vh] flex-col gap-3 overflow-y-auto p-4"
        >
          {!conversation && (
            <Empty
              title="Describe the workload you need"
              hint="For example: “I need a private AI server for 50 employees running Llama with confidential company data.”"
            />
          )}
          {conversation?.messages.map((message) => (
            <Message
              key={message.id}
              role={message.role}
              content={message.content}
              at={message.at}
            />
          ))}
          {busy && <Spinner label="Thinking" />}
          <div ref={bottom} />
        </Card>

        {questions.length > 0 && (
          <Card title="Quick answers" subtitle="Only what changes the recommendation">
            <div className="space-y-3">
              {questions.map((question) => (
                <div key={question.field}>
                  <p className="text-sm text-ink">{question.question}</p>
                  <p className="text-xs text-muted">{question.why}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {question.kind === 'choice' &&
                      question.options.map((choice) => (
                        <button
                          key={choice}
                          type="button"
                          className="btn px-2 py-1 text-xs"
                          disabled={busy}
                          onClick={() => answer(question.field, choice)}
                        >
                          {choice}
                        </button>
                      ))}
                    {question.kind === 'boolean' &&
                      ['yes', 'no'].map((choice) => (
                        <button
                          key={choice}
                          type="button"
                          className="btn px-2 py-1 text-xs"
                          disabled={busy}
                          onClick={() => answer(question.field, choice)}
                        >
                          {choice}
                        </button>
                      ))}
                    {question.kind === 'number' &&
                      [5, 10, 20, 50, 100].map((choice) => (
                        <button
                          key={choice}
                          type="button"
                          className="btn px-2 py-1 text-xs"
                          disabled={busy}
                          onClick={() => answer(question.field, choice)}
                        >
                          {choice}
                        </button>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        )}

        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            void send(draft)
          }}
        >
          <input
            className="input"
            placeholder="Describe the workload, or answer the question above…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={busy}
            aria-label="Message the Copilot"
          />
          <button className="btn btn-primary" type="submit" disabled={busy || !draft.trim()}>
            Send
          </button>
        </form>

        {error && <Banner tone="critical">{error}</Banner>}

        {recommendation && (
          <section className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">
                Infrastructure recommendation
                <span className="ml-2 text-xs font-normal text-muted">
                  policy engine: {recommendation.policy_engine}
                </span>
              </h2>
              <Banner tone="warning">Nothing is deployed until you approve an option.</Banner>
            </div>
            {recommendation.options.map((option) => (
              <OptionCard key={option.option} option={option} onSelect={select} />
            ))}
            {recommendation.options.length === 0 && (
              <Banner tone="critical">{recommendation.summary}</Banner>
            )}
            {recommendation.rejected_options.length > 0 && (
              <details className="card card-pad">
                <summary className="cursor-pointer text-xs text-ink-secondary">
                  {recommendation.rejected_options.length} option(s) were considered and rejected
                </summary>
                <div className="mt-3 space-y-3">
                  {recommendation.rejected_options.map((option) => (
                    <OptionCard key={option.option} option={option} compact />
                  ))}
                </div>
              </details>
            )}
          </section>
        )}
      </div>

      <aside className="space-y-4">
        <Card title="What the Copilot understood">
          {intentEntries.length === 0 ? (
            <p className="text-xs text-muted">Nothing yet.</p>
          ) : (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {intentEntries.map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="text-muted">{key}</dt>
                  <dd className="text-ink">{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          {conversation && (
            <p className="mt-3 text-[11px] text-muted">
              {conversation.ready
                ? 'Enough information to recommend.'
                : 'Waiting on the questions above.'}
            </p>
          )}
        </Card>

        <Card title="Demo scenarios" subtitle="Scripted requests, real engine">
          <div className="space-y-2">
            {(scenarios ?? []).map((scenario) => (
              <button
                key={scenario.key}
                type="button"
                className="w-full rounded-md border border-hairline px-2.5 py-2 text-left transition hover:bg-raised"
                disabled={busy}
                onClick={() => {
                  setConversation(null)
                  setRecommendation(null)
                  void send(scenario.prompt)
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-ink">
                    {scenario.key}. {scenario.title}
                  </span>
                  {scenario.expected_runtime && (
                    <span className="text-[10px] text-muted">
                      {scenario.expected_runtime.replaceAll('_', ' ').toLowerCase()}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-[11px] text-muted">{scenario.summary}</p>
              </button>
            ))}
          </div>
        </Card>

        {capabilities && (
          <Card title="Execution reality">
            <div className="space-y-1.5">
              {[
                ['Copilot', capabilities.copilot],
                ['Governor', capabilities.governor],
                ['Kubernetes', capabilities.kubernetes],
                ['OpenStack', capabilities.openstack],
                ['openCenter', capabilities.opencenter],
                ['GPU (cluster)', capabilities.kubernetes_gpu],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between gap-2">
                  <span className="text-xs text-muted">{label}</span>
                  <StatusBadge value={String(value)} />
                </div>
              ))}
            </div>
          </Card>
        )}
      </aside>
    </div>
  )
}
