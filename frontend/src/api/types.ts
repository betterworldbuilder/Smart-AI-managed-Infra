// Types mirroring the backend's pydantic models. Only the fields the UI reads
// are declared; everything else is allowed through unchecked.

export type Capabilities = {
  mode: 'simulation' | 'mvp' | 'production'
  mode_label: string
  copilot: string
  governor: string
  policy: string
  policy_engine: string
  human_approval: string
  kubernetes: string
  kubernetes_cluster: string
  flux: string
  opencenter: string
  deployment_engine: string
  openstack: string
  genestack: string
  ceph: string
  host_gpu: string
  host_gpu_models: string[]
  kubernetes_gpu: string
  kubernetes_gpu_reason: string
  llm_provider: string
  llm_model: string | null
  auth_enabled: boolean
  simulated_datacenter: boolean
}

export type RealityRow = { component: string; poc: string; current: string }

export type Capacity = { total: number; available: number }

export type GPUDevice = {
  id: string
  vendor: string
  model: string
  memory_gb: number
  host: string
  pci_address: string | null
  status: string
  allocation_type: string
  platform: string
  workload: string | null
  utilization_pct: number
  memory_used_gb: number
  temperature_c: number | null
}

export type GlobalInventory = {
  cpu: Capacity
  ram_gb: Capacity
  gpu: Capacity & { devices: GPUDevice[] }
  storage: { total_tb: number; available_tb: number; pools: StoragePool[] }
  platforms: Record<string, PlatformInventory>
  simulated: boolean
  generated_at: string | null
}

export type StoragePool = {
  name: string
  backend: string
  total_tb: number
  available_tb: number
  consumer: string
}

export type ComputeNode = {
  name: string
  platform: string
  cpu_total: number
  cpu_available: number
  ram_gb_total: number
  ram_gb_available: number
  gpu_ids: string[]
  labels: Record<string, string>
  availability_zone: string | null
  status: string
}

export type PlatformInventory = {
  platform: string
  available: boolean
  name: string | null
  version: string | null
  nodes: ComputeNode[]
  cpu: Capacity
  ram_gb: Capacity
  gpu: Capacity & { devices: GPUDevice[] }
  storage: { total_tb: number; available_tb: number }
  details: Record<string, unknown>
}

export type WizardQuestion = {
  field: string
  question: string
  why: string
  kind: 'text' | 'number' | 'choice' | 'boolean'
  options: string[]
  priority: number
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  at: string
  meta: Record<string, unknown>
}

export type WorkloadIntent = {
  name?: string | null
  description?: string | null
  workload_type: string
  environment: string
  operating_system?: string | null
  cpu_required: boolean
  gpu_required: boolean
  estimated_vcpu?: number | null
  estimated_ram_gb?: number | null
  gpu_count?: number | null
  gpu_memory_gb?: number | null
  preferred_gpu?: string | null
  storage_gb?: number | null
  high_availability: boolean
  sensitive_data: string
  internet_access: boolean
  network_exposure: string
  expected_users?: number | null
  concurrent_users?: number | null
  workload_isolation: string
  optimization_goal: string
  latency_sensitive: boolean
  container_compatible?: boolean | null
  replicas?: number | null
}

export type Conversation = {
  id: string
  title: string
  created_at: string
  updated_at: string
  messages: ChatMessage[]
  intent: WorkloadIntent
  provided: string[]
  pending_questions: WizardQuestion[]
  recommendation_id: string | null
  ready: boolean
  llm_provider: string
}

export type ResourcePlan = {
  runtime: string
  replicas: number
  vcpu_per_replica: number
  ram_gb_per_replica: number
  gpu_per_replica: number
  gpu_model: string | null
  gpu_memory_gb: number | null
  gpu_allocation_type: string | null
  storage_gb: number
  storage_backend: string
  network_exposure: string
  flavor: string | null
  image: string | null
  namespace: string | null
  database_engine: string | null
  notes: string[]
}

export type ScoreBreakdown = {
  compatibility: number
  capacity: number
  performance: number
  isolation: number
  reliability: number
  efficiency: number
  cost_penalty: number
  scarcity_penalty: number
  policy_delta: number
}

export type PolicyDecision = {
  rule_id: string
  title: string
  effect: string
  message: string
  score_delta: number
}

export type RecommendationOption = {
  option: string
  runtime: string
  title: string
  score: number
  viable: boolean
  recommended: boolean
  reason: string[]
  risks: string[]
  blocked_by: string[]
  resources: ResourcePlan
  breakdown: ScoreBreakdown
  capacity_impact: {
    vcpu_required: number
    vcpu_available: number
    ram_gb_required: number
    ram_gb_available: number
    gpu_required: number
    gpu_available: number
    storage_gb_required: number
    storage_gb_available: number
    fits: boolean
  }
  policy_decisions: PolicyDecision[]
  relative_cost_units: number
  native_scheduler: string
}

export type Recommendation = {
  id: string
  conversation_id: string | null
  created_at: string
  intent: WorkloadIntent
  options: RecommendationOption[]
  rejected_options: RecommendationOption[]
  open_questions: WizardQuestion[]
  summary: string
  policy_engine: string
}

export type PlanChange = { action: string; kind: string; name: string; detail: string | null }

export type DeploymentPlan = {
  id: string
  cluster_id: string
  changes: PlanChange[]
  artifacts: { path: string; content: string; language: string; description: string | null }[]
  issues: { severity: string; code: string; message: string }[]
  gitops: Record<string, unknown>
  valid: boolean
}

export type DeploymentEvent = {
  id: string
  at: string
  level: string
  source: string
  message: string
}

export type Deployment = {
  id: string
  name: string
  created_at: string
  updated_at: string
  state: string
  runtime: string
  recommendation_id: string | null
  selected_option: string | null
  intent: WorkloadIntent
  option: RecommendationOption
  spec: Record<string, any> | null
  plan: DeploymentPlan | null
  placement: {
    scheduler: string
    node: string
    nodes: string[]
    platform: string
    gpu_ids: string[]
    reason: string | null
  } | null
  history: {
    at: string
    from_state: string | null
    to_state: string
    actor: { kind: string; name: string }
    note: string | null
  }[]
  events: DeploymentEvent[]
  progress: number
  health: string
  failure_reason: string | null
  allocated_gpu_ids: string[]
}

export type MetricPoint = { at: string; value: number }
export type MetricSeries = { name: string; unit: string; points: MetricPoint[] }

export type ClusterMetrics = {
  generated_at: string
  cpu_utilization_pct: number
  ram_utilization_pct: number
  gpu_utilization_pct: number
  gpu_memory_pct: number
  storage_utilization_pct: number
  gpu_allocated: number
  gpu_total: number
  openstack_vm_count: number
  openstack_gpu_vm_count: number
  kubernetes_pod_count: number
  kubernetes_gpu_pod_count: number
  database_count: number
  failed_workloads: number
  running_deployments: number
  alerts: number
  gpus: {
    gpu_id: string
    model: string
    host: string
    utilization_pct: number
    memory_used_gb: number
    memory_total_gb: number
    temperature_c: number | null
    workload: string | null
    status: string
  }[]
  series: Record<string, MetricSeries>
}

export type AdvisorRecommendation = {
  id: string
  deployment_id: string
  created_at: string
  kind: string
  severity: string
  title: string
  observation: string[]
  recommendation: string
  current: Record<string, unknown>
  proposed: Record<string, unknown>
  estimated_savings: string | null
  actions: string[]
  status: string
  simulation: Record<string, unknown> | null
}

export type RemediationAlert = {
  id: string
  at: string
  severity: string
  alertname: string
  resource: string
  summary: string
  context: Record<string, unknown>
  options: {
    key: string
    title: string
    description: string
    impact: string
    recommended: boolean
  }[]
  recommended_option: string | null
  status: string
  decision: string | null
}

export type AuditEvent = {
  id: string
  timestamp: string
  user: string
  actor_kind: string
  action: string
  deployment: string | null
  recommendation: string | null
  resources: Record<string, unknown>
  detail: Record<string, unknown>
  message: string
}

export type Scenario = {
  key: string
  title: string
  summary: string
  prompt: string
  expected_runtime: string | null
}

export type TopologyNode = {
  id: string
  label: string
  group: string
  detail: string
  reality: string
  active: boolean
}

export type Topology = {
  nodes: TopologyNode[]
  edges: { id: string; source: string; target: string; label: string; active: boolean }[]
  highlight: string[]
  deployment: { id: string; name: string; runtime: string; state: string } | null
}

export type Integration = {
  key: string
  name: string
  status: 'CONNECTED' | 'SIMULATED' | 'DISCONNECTED' | 'ERROR'
  detail: Record<string, any>
  configure?: string
}
