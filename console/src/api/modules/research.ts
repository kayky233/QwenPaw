import { request } from "../request";

// ── Types ──

export interface ResearchTaskSummary {
  id: string;
  title: string;
  solution_name: string;
  active_run_id: string | null;
  latest_run_id: string | null;
}

export interface ResearchTaskDetail extends ResearchTaskSummary {
  program: string;
  solution: string;
  evaluation: {
    passed: boolean;
    score: number | null;
    metrics: Record<string, unknown>;
    error: string;
  };
  history: ResearchHistoryEntry[];
}

export interface ResearchHistoryEntry {
  round: number;
  status: string;
  score: number;
}

export interface ResearchRunEvent {
  phase: string;
  timestamp: string;
  round: number | null;
  detail: string;
  sequence: number;
}

export interface ResearchRunOutcome {
  round: number;
  status: string;
  passed: boolean;
  baseline_score: number;
  candidate_score: number | null;
  improvement: number;
  metrics: Record<string, unknown>;
  error: string;
}

export interface ResearchRunState {
  id: string;
  task_id: string;
  agent_id: string;
  status: string;
  rounds: number;
  completed_rounds: number;
  outcomes: ResearchRunOutcome[];
  created_at: string;
  events: ResearchRunEvent[];
  current_round: number | null;
  phase: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string;
  research_brief?: {
    goal: string;
    current_behavior: string;
    root_cause_hypotheses: string[];
    candidate_directions: Array<{
      id: string;
      title: string;
      priority: number;
      risk: string;
      reason: string;
    }>;
    success_metrics: string[];
    iteration_budget: number;
    modifiable_files: string[];
    relevant_tests: string[];
  } | null;
}

export interface ResearchDialogState {
  plan_id: string;
  status: string;
  goal: string;
  task_id: string | null;
  task_title: string | null;
  run_id: string | null;
  brief: ResearchRunState["research_brief"];
  plan_markdown?: string | null;
  rounds: number;
  model: string | null;
  auto_pr: boolean;
  revision: number;
  content_hash: string;
  approved_revision: number | null;
  approved_content_hash: string;
  approved_by: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string;
  worktree_path: string;
  branch: string;
  commit_sha: string;
  pr_url: string;
  test_summary: string;
  error: string;
  events: Array<{ phase: string; detail: string; timestamp: string }>;
  created_at: string;
  updated_at: string;
}

// ── API ──

export const researchApi = {
  /** List all available research tasks */
  listTasks: () => request<ResearchTaskSummary[]>("/research/tasks"),

  /** Get full task details including program, current solution, baseline eval */
  getTask: (taskId: string) =>
    request<ResearchTaskDetail>(`/research/tasks/${taskId}`),

  /** Start a new research run */
  startRun: (
    taskId: string,
    body: { rounds: number; agent_id: string; model?: string },
  ) =>
    request<ResearchRunState>(`/research/tasks/${taskId}/runs`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Poll a run's current state */
  getRun: (runId: string) =>
    request<ResearchRunState>(`/research/runs/${runId}`),

  /** Cancel a running/queued run */
  cancelRun: (runId: string) =>
    request<ResearchRunState>(`/research/runs/${runId}/cancel`, {
      method: "POST",
    }),

  /** SSE stream URL for a run (returns the URL string, not fetched) */
  streamUrl: (runId: string) => `/api/research/runs/${runId}/stream`,

  /** Dialog-based research: submit goal → get plan_id (async, returns 202 immediately) */
  dialogResearch: (body: {
    goal: string;
    model?: string;
    rounds?: number;
    auto_pr?: boolean;
    session_id?: string;
    user_id?: string;
  }) =>
    request<{
      plan_id: string;
      status: string;
      stream_url: string;
    }>("/research/dialog", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Poll dialog planning status */
  dialogStatus: (planId: string) =>
    request<ResearchDialogState>(`/research/dialog/${planId}`),

  editDialogPlan: (
    planId: string,
    body: { plan_markdown: string; expected_revision: number },
  ) =>
    request<ResearchDialogState>(`/research/dialog/${planId}/plan`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  approveDialogPlan: (
    planId: string,
    body: {
      expected_revision: number;
      content_hash: string;
      idempotency_key: string;
    },
  ) =>
    request<ResearchDialogState>(`/research/dialog/${planId}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  rejectDialogPlan: (
    planId: string,
    body: { expected_revision: number; reason: string },
  ) =>
    request<ResearchDialogState>(`/research/dialog/${planId}/reject`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** SSE stream URL for dialog planning */
  dialogStreamUrl: (planId: string) => `/api/research/dialog/${planId}/stream`,
};
