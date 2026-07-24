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
  tags?: string[];
  success_criteria?: Record<string, string>;
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

export type AuditFinding = {
  name: string;
  kind: "skill" | "doc" | "hook" | "subagent";
  used_evidence: string;
  verdict: "useful" | "redundant" | "improve" | "unknown";
  note: string;
};

export type AuditFindings = {
  items?: AuditFinding[];
  parse_error?: string;
};

export type AuditRecord = {
  run_id: string;
  auditor_model: string;
  status: "running" | "done" | "failed";
  markdown: string;
  findings_json?: AuditFindings;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type ArtifactRecord = {
  run_id: string;
  name: string;
  path: string;
  media_type: string;
  size_bytes: number;
};

// The slice of data/runs/<id>/metadata.json the harness catalog reads.
export type RunMetadata = {
  skills_bundle?: string[];
  subagents?: string[];
  skills_disabled?: string[];
  skills_experimental?: string[];
  harness_fingerprint?: {
    governance_docs?: Record<string, string>;
    hooks?: Record<string, string>;
  };
  condition_hash?: string;
};

export type SandboxRecord = {
  run_id: string;
  kind: "terminal" | "desktop";
  status: "starting" | "running" | "stopped" | "failed";
  container_name: string;
  host_port: number | null;
  url: string;
  error: string | null;
};

export type HarnessSkill = {
  name: string;
  kind: string;
  description: string;
};

export type StagedSkillRecord = {
  name: string;
  description: string;
  content: string;
  status: "draft" | "testing" | "promoted" | "archived";
  created_at: string;
  updated_at: string;
};

export type StagedSkillPromoteResult = {
  name: string;
  written: string;
  synced: boolean;
  next_steps: string;
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
  skills_disabled?: string[];
  skills_extra?: string[];
};

export type RunCreatePayload = {
  run_id?: string;
  label?: string;
  config: RunConfigPayload;
};

export type ModelOption = {
  id: string;
  label: string;
};

export type ModelCatalog = {
  // "gateway" when the list came live from LiteLLM; "opencode.json" when the
  // gateway was unreachable and the routing map was used as a fallback.
  source: string;
  models: ModelOption[];
  default_executor: string;
};

export type DocEntry = {
  slug: string;
  title: string;
  description: string;
  group: string;
};

export type DocContent = {
  slug: string;
  title: string;
  content: string;
};

export type SwebenchProInstance = {
  instance_id: string;
  repo: string;
  repo_language: string;
  title: string;
  summary: string;
  issue_categories: string[];
  f2p_count: number;
  p2p_count: number;
  imported: boolean;
};

export type ImprovementProposal = {
  title: string;
  body: string;
  kind?: string;
  target?: string;
  repo: string;
  created_issue_url: string | null;
};

export type ImprovementsDoc = {
  run_id: string;
  context: { case: string; harness: string; executor_model: string };
  generator_model: string;
  proposals: ImprovementProposal[];
};

export type ProposalIssueField = {
  id: string;
  label: string;
  value: string;
  kind: "input" | "textarea" | "dropdown";
  recommended: boolean;
  reason: string;
};

export type ProposalIssueForm = {
  repository: string;
  template: string;
  title: string;
  url: string;
  fields: ProposalIssueField[];
};

export type ProposalIssue = {
  run_id: string;
  repository: string;
  markdown: string;
  form: ProposalIssueForm;
};

export type SavedProposalRecord = {
  id: number;
  run_id: string;
  title: string;
  body: string;
  kind: string;
  target: string;
  repo_hint: string;
  repo: string;
  created_issue_url: string | null;
  case: string;
  harness: string;
  executor_model: string;
  generator_model: string;
  run_created_at: string | null;
  template_version: string;
  template_fingerprint: string;
  status: "saved" | "issued";
  group_issue_id: number | null;
  created_at: string;
  updated_at: string;
};

export type SaveImprovementsResult = {
  run_id: string;
  saved: number;
  updated: number;
  total: number;
};

export type GroupIssueResult = {
  group_issue_id: number;
  repository: string;
  markdown: string;
  form: ProposalIssueForm;
  proposal_ids: number[];
  source_run_ids: string[];
};
