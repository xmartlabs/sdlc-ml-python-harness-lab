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
  // Verdict of the automated checks: false means the produced code was run and
  // failed. null when they have not run or the case has no objective criterion.
  // Without this a run that does not even start is indistinguishable in the
  // list from one merely waiting for its manual score.
  objective_passed?: boolean | null;
  // Why it failed, classified server-side (see run_failures.py) so the list,
  // the leaderboard and the API share one definition. "lab" means the run
  // measured nothing — it is excluded from the leaderboard and should be
  // retried rather than read as a result.
  failure_kind?: "lab" | "stalled" | "timeout" | "agent" | null;
  failure_label?: string | null;
  // Set when the run came from POST /api/runs/batch; null for a run created
  // singly and for every run that predates batching.
  batch_id?: string | null;
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

/**
 * One request that expands into the cross-product of its axes.
 *
 * Every array is a dimension; the scalars are shared by every generated run.
 * There is no `resume` and no `run_id`/`label`: resuming targets one specific
 * workspace, and N generated runs each take the next auto-assigned id.
 */
export type RunBatchPayload = {
  case_ids: string[];
  prompt_variants: string[];
  harnesses: string[];
  executor_models: (string | null)[];
  planner_models: (string | null)[];
  repetitions: number;
  score_mode: string;
  max_steps: number;
  dry_run: boolean;
  skills_disabled?: string[];
  skills_extra?: string[];
};

export type RunBatchResult = {
  batch_id: string;
  count: number;
  run_ids: string[];
};

/**
 * Verdict of the pre-creation check on a run configuration.
 *
 * These are the same checks `POST /api/runs` applies, so `ok: true` means the
 * form will not be rejected for configuration reasons; it is not a promise that
 * the run itself will succeed.
 */
export type RunValidationResult = {
  ok: boolean;
  detail: string;
};

export type ModelOption = {
  id: string;
  label: string;
  // False when neither opencode.json nor the enabled web catalog can route the
  // id: the gateway serves it, but a run using it would fail at launch.
  routable?: boolean;
  // False when the machine behind a gateway card is provably not serving it
  // right now — the LAN host is down, or the alias names a model it does not
  // have. Null when undecidable (a cloud card, a host the lab does not probe),
  // which is why this is not just `routable` with a wider meaning: one asks
  // whether the id can be addressed, the other whether anything is listening.
  reachable?: boolean | null;
  unreachable_reason?: string | null;
  // Other gateway ids that the router resolves to this same backend model.
  // They are hidden from the picker so one model appears once.
  aliases?: string[];
  // Serving backend ("lmstudio" | "cloud" | "ollama") and its display heading.
  // The API returns models pre-sorted by group_rank, so rendering in order
  // reproduces the intended hierarchy: LM Studio → Cloud → Ollama.
  backend?: string;
  group?: string;
  group_rank?: number;
  // The picker's two upper tiers. `source` is how the model is *reached* —
  // through the LiteLLM gateway, or straight at a local host — which is a
  // different axis from ModelCatalog.source (where the *list* came from).
  // `provider` is the vendor: lmstudio, ollama, openai, anthropic, google,
  // nvidia, other.
  source?: "gateway" | "direct";
  provider?: string;
  provider_label?: string;
  // Direct models only: what the host reports about the model right now.
  loaded?: boolean;
  loaded_context?: number | null;
  max_context?: number | null;
};

/**
 * Outcome of measuring the context window a model is *actually* serving.
 *
 * `status` is "ok" when the model accepted a prompt of `required_tokens`,
 * "insufficient" when it refused one and named a smaller window, and "unknown"
 * when the probe could not reach a verdict — the run gate only blocks on
 * "insufficient", so an unreachable backend never stops a run.
 */
export type ContextProbeResult = {
  model: string;
  status: "ok" | "insufficient" | "unknown";
  ok: boolean;
  required_tokens: number;
  context_tokens: number | null;
  prompt_tokens: number | null;
  detail: string;
};

export type ModelCatalog = {
  // "gateway" when the list came live from LiteLLM; "opencode.json" when the
  // gateway was unreachable and the routing map was used as a fallback.
  source: string;
  models: ModelOption[];
  default_executor: string;
  // Why a direct provider has no models (server off, URL unset), keyed by
  // provider id. Per provider because one host can be up while the other is
  // down — an empty group with no reason is unactionable.
  direct_errors?: Record<string, string>;
};

/** One model as the LM Studio host reports it, with no gateway in between. */
export type LMStudioModel = {
  // Prefixed id the run form uses ("lmstudio/prism-ml/bonsai-27b").
  id: string;
  // The id LM Studio itself registers ("prism-ml/bonsai-27b").
  bare_id: string;
  type: string;
  state: string;
  loaded: boolean;
  // The window it is serving right now; null unless it is loaded, because an
  // unloaded model gets whatever default a JIT auto-load picks.
  loaded_context: number | null;
  max_context: number | null;
  arch: string;
  quantization: string;
};

export type LMStudioCatalog = {
  base_url: string;
  reachable: boolean;
  error: string | null;
  models: LMStudioModel[];
};

/** One model as the Ollama host reports it, with no gateway in between. */
export type OllamaModel = {
  // Prefixed id the run form uses ("ollama/qwen3:32b").
  id: string;
  // The id Ollama itself registers ("qwen3:32b").
  bare_id: string;
  state: string;
  running: boolean;
  // The window the server serves for this model; only knowable while it runs.
  // Anything else gets the host's OLLAMA_CONTEXT_LENGTH, which defaults to
  // 4096 — far below what a run needs, and not readable from here.
  context_length: number | null;
  parameter_size: string;
  quantization: string;
  family: string;
};

export type OllamaCatalog = {
  base_url: string;
  reachable: boolean;
  error: string | null;
  models: OllamaModel[];
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
  // Seconds of silence, with no model request in flight, after which a run is
  // killed as stuck; 0 = disabled.
  run_stall_seconds: number;
  // The same silence while the model owes the run a first token. Much larger:
  // a local model says nothing while it processes the prompt.
  run_model_wait_seconds: number;
  // Consecutive failed provider requests tolerated before giving up.
  run_max_stream_errors: number;
  // Ceiling on the cross-product of one POST /api/runs/batch. Read rather
  // than mirrored in the client, so the two can never disagree.
  max_batch_runs: number;
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
  // Serving backend and its display heading. Rows arrive pre-sorted by
  // group_rank so the table sections match the run picker's ordering.
  backend?: string;
  group?: string;
  group_rank?: number;
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
