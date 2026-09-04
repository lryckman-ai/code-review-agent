// Fetch client for the codereview FastAPI service (api.py).
// Types here mirror the dicts returned by monitoring/db.py's list_runs(),
// get_run(), and get_stats_summary() — kept by hand in sync with the Python
// side since there's no shared schema between the two languages.

const API_BASE = import.meta.env.VITE_API_BASE

export interface AgentRun {
  agent: string
  latency_ms: number | null
  output_chars?: number
  recorded_at?: number
}

export interface RunSummary {
  run_id: string
  label: string
  repo: string | null
  pr_number: number | null
  started_at: number
  completed_at: number | null
  total_latency_ms: number | null
  gate_passed: number | null // SQLite has no native boolean: 0, 1, or null (not yet scored)
  status: string
  agents: AgentRun[]
  shadow_score: number | null
  real_cost_usd: number | null
  reference_cost_usd: number | null
}

export interface GateDecision {
  gate: string
  score: number
  threshold: number
  passed: number
  breakdown: Record<string, number> | null
  notes: string
  key_issues: string[]
  recorded_at: number
}

export interface CostEstimate {
  prompt_tokens: number | null
  completion_tokens: number | null
  real_cost_usd: number
  reference_model: string | null
  reference_cost_usd: number | null
  recorded_at: number
}

export interface RunDetail extends RunSummary {
  code_hash: string
  code_lines: number
  output: string
  gate_decision: GateDecision | null
  cost_estimate: CostEstimate | null
}

export interface TrendPoint {
  day: string
  runs: number
  avg_latency_ms: number
}

export interface StatsSummary {
  total_runs: number
  avg_latency_ms: number | null
  gate_pass_rate: number | null
  gate_scored_runs: number
  avg_real_cost_usd: number | null
  avg_reference_cost_usd: number | null
  trend: TrendPoint[]
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export function fetchRuns(limit = 50): Promise<RunSummary[]> {
  return getJSON<RunSummary[]>(`/api/runs?limit=${limit}`)
}

export function fetchRun(runId: string): Promise<RunDetail> {
  return getJSON<RunDetail>(`/api/runs/${runId}`)
}

export function fetchStatsSummary(): Promise<StatsSummary> {
  return getJSON<StatsSummary>('/api/stats/summary')
}
