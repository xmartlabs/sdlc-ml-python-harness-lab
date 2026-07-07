export type RunState =
  | "draft"
  | "pending"
  | "running"
  | "awaiting_manual_score"
  | "scoring"
  | "auditing"
  | "completed"
  | "failed"
  | "cancelled";

export type CaseRecord = {
  case_id: string;
  name: string;
  type: string;
  status: string;
  sync_state: string;
  description: string;
  prompt_variants?: Record<string, string>;
};

export type CaseCreatePayload = {
  case_id: string;
  name: string;
  type: string;
  description: string;
  tags: string[];
  prompt_variants: Record<string, string>;
  step_prompts: string[];
  success_criteria: Record<string, string>;
  scoring_rubric: string;
  constraints: string[];
  validation_commands: string[];
  max_steps: number;
};

export type RunRecord = {
  run_id: string;
  state: RunState;
  command: string[];
  started_at?: string | null;
  ended_at?: string | null;
  exit_code?: number | null;
  score?: number | null;
  interventions?: number | null;
  notes?: string;
  failure_reason?: string | null;
};

export type RunEventRecord = {
  id: number;
  run_id: string;
  level: string;
  message: string;
  created_at: string;
};

export type ValidationCheck = {
  name: string;
  passed: boolean;
  exit_code: number | null;
  detail: string;
};

export type ValidationRecord = {
  run_id: string;
  objective: {
    strategy?: string;
    passed?: boolean | null;
    summary?: string;
    checks?: ValidationCheck[];
  };
  cost: {
    available?: boolean;
    total_tokens?: number | null;
    cost_usd?: number | null;
    time_minutes?: number | null;
  };
  effort: { steps?: number | null; interventions?: number | null };
  subjective: { functional_score?: number | null; notes?: string };
  composite: number | null;
};

export type AttributionRecord = {
  run_id: string;
  tools: Record<string, number>;
  files: { read?: string[]; edited?: string[] };
  bash_commands: Record<string, number>;
  agent_modes: Record<string, number>;
  harness_usage: {
    governance_docs_available?: string[];
    governance_docs_read?: string[];
    governance_coverage?: string;
    skills_available?: string[];
    skills_consulted?: string[];
    skills_note?: string;
  };
  summary: string;
  steps_parsed: number;
};

export type RunConfigPayload = {
  case_id: string;
  prompt_variant: string;
  harness: string;
  planner_model: string | null;
  executor_model: string | null;
  score_mode: string;
  max_steps: number;
  resume: boolean;
  dry_run: boolean;
};

export type RunCreatePayload = {
  run_id: string;
  config: RunConfigPayload;
};
