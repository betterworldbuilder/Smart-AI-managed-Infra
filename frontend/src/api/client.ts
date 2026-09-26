import type {
  AdvisorRecommendation,
  AuditEvent,
  Capabilities,
  ClusterMetrics,
  Conversation,
  Deployment,
  GlobalInventory,
  Integration,
  RealityRow,
  Recommendation,
  RemediationAlert,
  Scenario,
  Topology,
  WizardQuestion,
  WorkloadIntent,
} from './types'

const TOKEN_KEY = 'aiinfra.token'
const BASE = '/api'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* private mode: the session simply will not persist */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${BASE}${path}`, { ...init, headers })
  if (response.status === 401) {
    setToken(null)
    throw new ApiError(401, 'Session expired -- please sign in again.')
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* keep the status text */
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  // --- system ---------------------------------------------------------
  health: () => request<Record<string, any>>('/health'),
  capabilities: () => request<Capabilities>('/system/capabilities'),
  realityMatrix: () => request<RealityRow[]>('/system/reality-matrix'),
  lifecycle: () => request<Record<string, any>[]>('/system/lifecycle'),
  integrations: () => request<Integration[]>('/settings/integrations'),
  login: (username: string, password: string) =>
    post<{ access_token: string; username: string; warning: string }>('/auth/login', {
      username,
      password,
    }),
  me: () => request<{ username: string; auth_enabled: boolean; warning: string | null }>('/auth/me'),

  // --- inventory ------------------------------------------------------
  inventory: () => request<GlobalInventory>('/inventory/global'),
  inventoryTree: () => request<Record<string, any>>('/inventory/tree'),
  kubernetes: () => request<Record<string, any>>('/inventory/kubernetes'),
  openstack: () => request<Record<string, any>>('/inventory/openstack'),
  genestack: () => request<Record<string, any>>('/inventory/genestack'),
  ceph: () => request<Record<string, any>>('/inventory/ceph'),
  gpus: () => request<Record<string, any>>('/inventory/gpus'),
  hostGpu: () => request<Record<string, any>>('/inventory/host-gpu'),
  topology: (deploymentId?: string) =>
    request<Topology>(`/topology${deploymentId ? `?deployment_id=${deploymentId}` : ''}`),

  // --- copilot --------------------------------------------------------
  conversations: () => request<Conversation[]>('/copilot/conversations'),
  conversation: (id: string) => request<Conversation>(`/copilot/conversations/${id}`),
  newConversation: () => post<Conversation>('/copilot/conversations'),
  sendMessage: (message: string, conversationId?: string | null) =>
    post<{ conversation: Conversation; recommendation: Recommendation | null }>(
      '/copilot/messages',
      { message, conversation_id: conversationId ?? null },
    ),
  answer: (conversationId: string, answers: Record<string, unknown>) =>
    post<{ conversation: Conversation; recommendation: Recommendation | null }>(
      `/copilot/conversations/${conversationId}/answers`,
      { answers },
    ),
  setIntent: (conversationId: string, intent: WorkloadIntent) =>
    post<{ conversation: Conversation; recommendation: Recommendation | null }>(
      `/copilot/conversations/${conversationId}/intent`,
      intent,
    ),
  questions: () => request<WizardQuestion[]>('/copilot/questions'),
  profiles: () => request<Record<string, any>[]>('/copilot/profiles'),

  // --- recommendations ------------------------------------------------
  recommend: (intent: WorkloadIntent) => post<Recommendation>('/recommendations', intent),
  recommendations: () => request<Recommendation[]>('/recommendations'),
  recommendation: (id: string) => request<Recommendation>(`/recommendations/${id}`),

  // --- deployments ----------------------------------------------------
  deployments: () => request<Deployment[]>('/deployments'),
  deployment: (id: string) => request<Deployment>(`/deployments/${id}`),
  createDeployment: (recommendationId: string, option: string, name?: string) =>
    post<Deployment>('/deployments', {
      recommendation_id: recommendationId,
      option,
      name: name ?? null,
    }),
  approve: (id: string, note = '') => post<Deployment>(`/deployments/${id}/approve`, { note }),
  applyDeployment: (id: string, note = '') => post<Deployment>(`/deployments/${id}/apply`, { note }),
  reject: (id: string, note = '') => post<Deployment>(`/deployments/${id}/reject`, { note }),
  rollback: (id: string, note = '') => post<Deployment>(`/deployments/${id}/rollback`, { note }),
  modify: (id: string, intent: WorkloadIntent) =>
    post<{ deployment: Deployment; recommendation: Recommendation }>(
      `/deployments/${id}/modify`,
      intent,
    ),
  deploymentSpec: (id: string) => request<{ spec: any; yaml: string }>(`/deployments/${id}/spec`),
  deploymentPlan: (id: string) =>
    request<{ plan: any; changes: string[] }>(`/deployments/${id}/plan`),
  deploymentArtifacts: (id: string) =>
    request<{ path: string; content: string; language: string; description: string | null }[]>(
      `/deployments/${id}/artifacts`,
    ),
  deploymentAudit: (id: string) => request<AuditEvent[]>(`/deployments/${id}/audit`),
  deploymentMetrics: (id: string) => request<Record<string, any>>(`/deployments/${id}/metrics`),
  deploymentStatus: (id: string) => request<Record<string, any>>(`/deployments/${id}/status`),
  gitops: () => request<Record<string, any>>('/gitops'),

  // --- observability ---------------------------------------------------
  metrics: () => request<ClusterMetrics>('/metrics/summary'),
  workloadMetrics: () => request<Record<string, any>[]>('/metrics/workloads'),
  advice: () => request<AdvisorRecommendation[]>('/advisor'),
  analyse: () => post<AdvisorRecommendation[]>('/advisor/analyse'),
  simulateAdvice: (id: string) => post<AdvisorRecommendation>(`/advisor/${id}/simulate`),
  ignoreAdvice: (id: string) => post<AdvisorRecommendation>(`/advisor/${id}/ignore`),
  alerts: () => request<RemediationAlert[]>('/remediation/alerts'),
  simulateGpuFailure: () => post<RemediationAlert>('/remediation/simulate-gpu-failure'),
  decideAlert: (id: string, option: string) =>
    post<RemediationAlert>(`/remediation/alerts/${id}/decide`, { option }),

  // --- misc -------------------------------------------------------------
  audit: () => request<AuditEvent[]>('/audit'),
  scenarios: () => request<Scenario[]>('/scenarios'),
  runScenario: (key: string) => post<Record<string, any>>(`/scenarios/${key}/run`),
}
