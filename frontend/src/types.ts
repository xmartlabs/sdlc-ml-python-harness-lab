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
  // API timestamps are naive UTC strings (no offset) — parse them with
  // dates.ts/parseApiDate, never with a bare `new Date(...)`.
  created_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  exit_code?: number | null;
  score?: number | null;
  interventions?: number | null;
  notes?: string;
  failure_reason?: string | null;
  // Improvement-synthesis status, derived from data/runs/<id>/improvements.json:
  // "yes" = proposed changes, "no" = ran but proposed none, "pending" = not run.
  improvements?: "yes" | "no" | "pending";
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
  subjective: {
    functional_score?: number | null;
    notes?: string;
    score_source?: "manual" | "objective" | "llm_judge" | string;
  };
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
  /** Mirror of SKILL.md. Kept for compatibility; the file list is the truth. */
  content: string;
  status: "draft" | "testing" | "promoted" | "archived";
  content_hash: string;
  source_url: string;
  source_ref: string;
  source_kind: string;
  imported_at: string | null;
  created_at: string;
  updated_at: string;
};

export type StagedSkillFileRecord = {
  path: string;
  content: string;
  /** "utf-8" for text; "base64" for binary assets, which the UI cannot edit. */
  encoding: string;
  sha256: string;
  size: number;
};

/** One skill inside a source that holds several — a catalog repo. */
export type StagedSkillImportChoice = {
  name: string;
  path: string;
  description: string;
};

/** What an unconfirmed import returns: reviewed by the user, never written. */
export type StagedSkillImportPreview = {
  preview: true;
  choices: StagedSkillImportChoice[];
  name: string;
  description: string;
  source_url: string;
  source_ref: string;
  source_kind: string;
  warnings: string[];
  total_bytes: number;
  files: { path: string; size: number }[];
};

/**
 * A catalog source that has not been narrowed down yet. Not an error: the user
 * picks one of `choices` and the same source is previewed again.
 */
export type StagedSkillImportChoices = {
  preview: false;
  choices: StagedSkillImportChoice[];
  detail: string;
};

export type StagedSkillPromoteResult = {
  name: string;
  written: string[];
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
  // False when neither opencode.json nor the enabled web catalog can route the
  // id: the gateway serves it, but a run using it would fail at launch.
  routable?: boolean;
};

export type ModelCatalog = {
  // "gateway" when the list came live from LiteLLM; "opencode.json" when the
  // gateway was unreachable and the routing map was used as a fallback.
  source: string;
  models: ModelOption[];
  default_executor: string;
};

export type EnvSettingItem = {
  key: string;
  label: string;
  group: string;
  is_secret: boolean;
  help_text: string;
  is_set: boolean;
  // Secrets come back masked; writing the mask back means "leave unchanged".
  value: string;
};

/**
 * `auth_enabled` false means the lab runs in open mode — no password has been
 * set, so every request is allowed and the UI shows a warning banner.
 */
export type SessionState = {
  authenticated: boolean;
  auth_enabled: boolean;
};

export type EnvSettings = {
  items: EnvSettingItem[];
  warnings: string[];
  env_path: string;
};

export type EnvUpdateResult = EnvSettings & {
  written: string[];
  backup: string | null;
};

export type RuntimeFacts = {
  repo_root: string;
  database_url: string;
  execution_backend: string;
  runner_image: string;
  sandbox_image: string;
  docker_host_alias: string;
  redis_url: string;
  run_timeout_seconds: number;
};

export type GatewayModelRecord = {
  id: number;
  model_id: string;
  label: string;
  enabled: boolean;
  is_default: boolean;
  live: boolean;
  options_json: Record<string, unknown>;
  first_seen_at: string;
  last_seen_at: string;
};

export type ModelRefreshResult = {
  added: string[];
  stale: string[];
  total: number;
  gateway_reachable: boolean;
};

export type GatewayTestResult = {
  ok: boolean;
  detail?: string;
  model_count?: number;
  sample?: string[];
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

// One flat leaderboard row per terminal run (GET /api/leaderboard); the
// Dashboard groups by condition (harness · model · prompt · version) client-side.
export type LeaderboardRunRow = {
  run_id: string;
  case_id: string;
  case_type: string;
  harness: string;
  prompt_variant: string;
  executor_model: string | null;
  planner_model: string | null;
  harness_version: string;
  // true/false = objective verdict; null = manual-score case (no % resolved).
  resolved: boolean | null;
  score: number | null;
  composite: number | null;
  cost_usd: number | null;
  total_tokens: number | null;
  steps: number | null;
  state: string;
  created_at: string;
};

// Curated official swebench.com rows (data/reference/swebench_official.json).
export type OfficialReferenceRow = {
  dataset: string;
  agent: string;
  model: string;
  pct_resolved: number;
  date?: string;
  source_url?: string;
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
  strictness?: string;
  gate?: { skipped: boolean; reason: string | null; min_score: number };
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

/** One row of the health panel (ADR-0018). */
export type HealthCheck = {
  id: string;
  label: string;
  status: "ok" | "warn" | "error" | "unknown";
  detail: string;
  latency_ms: number | null;
  /** The allowlisted repair for this row, or null when nothing to fix. */
  heal: string | null;
  /** Offending paths / run ids, when the check can name them. */
  items: string[];
};

export type HealthReport = {
  status: "ok" | "warn" | "error" | "unknown";
  checks: HealthCheck[];
};

export type HealResult = {
  action: string;
  ok: boolean;
  detail: string;
};
