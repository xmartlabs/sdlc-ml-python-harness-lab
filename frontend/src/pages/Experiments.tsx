import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { ModelPicker } from "../components/ModelPicker";
import { Spinner } from "../components/Spinner";
import {
  IconAlert,
  IconCheck,
  IconMinus,
  IconTrendDown,
  IconTrendUp,
  IconX
} from "../components/icons";
import {
  HARNESS_NATIVE_MODELS,
  HARNESS_OPTIONS,
  HOST_HARNESSES
} from "../harnesses";
import type {
  AggregateRecord,
  CaseOutcomeCell,
  ConditionSummary,
  DeltaRecord,
  ExperimentRecord,
  ExperimentSummary,
  LaunchConfigSummary,
  ModelOption,
  RunConfigPayload,
  RunValidationResult,
  ValidityBlock,
  VerdictBlock
} from "../types";

const MODE_LABEL: Record<string, string> = {
  harness_evaluation: "Harness evaluation",
  cross_repo: "Cross-repo",
  discovery: "Discovery",
  subject_validation: "Subject validation"
};

/** What each mode does and does not license — shown, not assumed known. */
const MODE_CAVEAT: Record<string, string> = {
  harness_evaluation:
    "Repository, case and model held fixed; the harness varies. The only mode that supports a causal claim — and only with repetitions.",
  cross_repo:
    "Harness and model held fixed; the repository varies. Read per repository: scores from different codebases are not equivalent conditions and must never be pooled.",
  discovery:
    "No control. Exploratory evidence, not experimental evidence — this mode describes, it does not demonstrate.",
  subject_validation:
    "No control, nothing ablated. Reports how reliably the declared subject passed under each harness/model arm — one rate per arm, never pooled, and nothing about the harness."
};

function formatNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return Number(value.toPrecision(digits)).toString();
}

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(0)}%`;
}

/** A mean is never shown without its spread. */
function spread(aggregate: AggregateRecord | undefined): string {
  if (!aggregate || aggregate.mean === null) {
    return "—";
  }
  if (aggregate.stdev === null) {
    return formatNumber(aggregate.mean);
  }
  return `${formatNumber(aggregate.mean)} ± ${formatNumber(aggregate.stdev, 2)}`;
}

function deltaLabel(delta: DeltaRecord): string {
  if (delta.absolute === null) {
    return "—";
  }
  const sign = delta.absolute > 0 ? "+" : "";
  return `${sign}${formatNumber(delta.absolute)}`;
}

/**
 * Improvement is read from the metric's declared direction, never from the sign.
 * A drop in `cost_usd` is a win; a drop in `test_pass_rate` is not.
 */
function deltaClass(delta: DeltaRecord): string {
  if (delta.absolute === null || delta.direction === "neutral") {
    return "";
  }
  const better =
    delta.direction === "higher_is_better" ? delta.absolute > 0 : delta.absolute < 0;
  return better ? "delta-better" : "delta-worse";
}

/** A pass/fail cell, as a shape rather than an emoji that renders differently everywhere. */
function Outcome({ value }: { value: boolean | null }) {
  if (value === null) {
    return <span className="outcome outcome--unknown" title="Not validated">·</span>;
  }
  return (
    <span
      className={`outcome outcome--${value ? "pass" : "fail"}`}
      title={value ? "Passed validation" : "Failed validation"}
    >
      {value ? <IconCheck size={15} /> : <IconX size={15} />}
    </span>
  );
}

const MOVEMENT_CLASS: Record<string, string> = {
  fixed: "movement movement--fixed",
  broken: "movement movement--broken",
  unchanged: "movement movement--unchanged",
  incomplete: "movement movement--incomplete"
};

/**
 * The ten-second answer, at the top of the page.
 *
 * Everything here comes from the backend's own verdict: what the experiment is
 * allowed to claim follows from its mode and its repetition count, and that is
 * not a judgement a template should be making. The wording is deliberately
 * cautious — "likely improvement", "within observed variance" — because this
 * lab does not run hypothesis tests and must not sound as though it does.
 */
/** A rate reads as a percentage, a cost as money, everything else as a number. */
function formatHighlight(metric: string, value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (metric.endsWith("_rate") || metric.endsWith("_ratio")) {
    return formatPercent(value);
  }
  if (metric.includes("cost") || metric.endsWith("_usd")) {
    return `$${value.toFixed(2)}`;
  }
  return formatNumber(value);
}

/**
 * The per-arm, per-case grid for a mode with no control: counts per cell, with
 * the repetitions kept. There is no movement column because there is nothing
 * to move relative to.
 */
function CaseOutcomeGrid({
  outcomes
}: {
  outcomes: Record<string, Record<string, CaseOutcomeCell>>;
}) {
  const labels = Object.keys(outcomes);
  const caseIds = [
    ...new Set(labels.flatMap((label) => Object.keys(outcomes[label] ?? {})))
  ];
  if (caseIds.length === 0) {
    return null;
  }
  return (
    <div className="card">
      <h3>Per case, per arm</h3>
      <p className="muted small">
        Passed of scored, for every cell. Each arm is read down its own column —
        the columns are not combined.
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th>Case</th>
            {labels.map((label) => (
              <th key={label}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {caseIds.map((caseId) => (
            <tr key={caseId}>
              <td className="mono small">{caseId}</td>
              {labels.map((label) => {
                const cell = outcomes[label]?.[caseId];
                return (
                  <td key={label}>
                    {!cell || cell.scored_runs === 0
                      ? "—"
                      : `${cell.successes} of ${cell.scored_runs}`}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const VALIDITY_TITLE: Record<string, string> = {
  treatment_not_activated: "The treatment never happened",
  infrastructure_failures: "These runs measured the connection",
  no_headroom: "No headroom to detect an improvement",
  mixed_treatment: "The arm mixes treated and untreated runs",
  design_objection: "The design was objected to when it was created"
};

/**
 * Read before the verdict (ADR-0046).
 *
 * Deliberately its own card above the verdict rather than another line in
 * `verdict.caveats`: a caveat printed under a confident headline is exactly
 * how this lab published three comparisons that could not have measured what
 * they named.
 */
function ValidityBanner({ validity }: { validity: ValidityBlock }) {
  if (validity.status === "valid" || validity.issues.length === 0) return null;
  const invalid = validity.status === "invalid";
  return (
    <div className={`card validity ${invalid ? "validity--invalid" : "validity--degraded"}`}>
      <div className="row space-between">
        <p className="verdict-kicker">
          {invalid ? "Comparison not valid" : "Read with care"}
        </p>
        <span className={`badge ${invalid ? "error" : "warn"}`}>{validity.status}</span>
      </div>
      <ul className="validity-issues">
        {validity.issues.map((issue) => (
          <li key={`${issue.code}:${issue.arm ?? ""}:${issue.detail}`}>
            <strong>
              <IconAlert size={14} /> {VALIDITY_TITLE[issue.code] ?? issue.code}
            </strong>
            <p>{issue.detail}</p>
            {issue.remedy ? <p className="muted">{issue.remedy}</p> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function VerdictCard({ verdict }: { verdict: VerdictBlock }) {
  // `tone` is authoritative since ADR-0046; the headline sniff below stays one
  // release longer so a summary from an older backend still renders in colour.
  const headline = verdict.headline.toLowerCase();
  const tone =
    verdict.tone ??
    (headline.includes("improvement")
      ? "better"
      : headline.includes("regression")
        ? "worse"
        : "neutral");
  const Trend =
    tone === "better" ? IconTrendUp : tone === "worse" ? IconTrendDown : IconMinus;
  const toneClass =
    tone === "better"
      ? "verdict--better"
      : tone === "worse"
        ? "verdict--worse"
        : "verdict--neutral";

  return (
    <div className={`card verdict ${toneClass}`}>
      <div className="verdict-head">
        <span className="verdict-icon">
          <Trend size={22} />
        </span>
        <div>
          <p className="verdict-kicker">Verdict</p>
          <h2>{verdict.headline}</h2>
          <p className="muted">{verdict.claim}</p>
          {verdict.observed ? (
            /* The measurement is demoted, never withheld (ADR-0041). */
            <p className="muted verdict-observed">Measured: {verdict.observed}</p>
          ) : null}
        </div>
        <span className="badge neutral verdict-strength">{verdict.strength}</span>
      </div>

      {verdict.highlights.length > 0 ? (
        <div className="verdict-highlights">
          {verdict.highlights.map((highlight) => (
            <div
              className="verdict-highlight"
              key={`${highlight.label}:${highlight.metric}`}
            >
              <span>{highlight.label}</span>
              <strong>
                {highlight.scored_runs === undefined ? (
                  <>
                    {formatHighlight(highlight.metric, highlight.control)} →{" "}
                    {formatHighlight(highlight.metric, highlight.treatment)}
                  </>
                ) : (
                  /* One arm on its own: a rate with nothing to compare it
                     against, so it is shown with the counts behind it rather
                     than as an arrow between two numbers. */
                  <>
                    {formatHighlight(highlight.metric, highlight.rate)}{" "}
                    <span className="muted">
                      ({highlight.successes} of {highlight.scored_runs})
                    </span>
                  </>
                )}
              </strong>
            </div>
          ))}
        </div>
      ) : null}

      <div className="verdict-movements">
        <span className="movement movement--fixed">{verdict.movements.fixed} fixed</span>
        <span className="movement movement--broken">
          {verdict.movements.broken} broken
        </span>
        <span className="movement movement--unchanged">
          {verdict.movements.unchanged} unchanged
        </span>
        {verdict.movements.incomplete > 0 ? (
          <span className="movement movement--incomplete">
            {verdict.movements.incomplete} incomplete
          </span>
        ) : null}
      </div>

      <p className="verdict-evidence muted">Evidence: {verdict.evidence}</p>

      {verdict.caveats.length > 0 ? (
        <ul className="warning-list">
          {verdict.caveats.map((caveat) => (
            <li key={caveat}>
              <IconAlert size={14} /> {caveat}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ConditionTable({ conditions }: { conditions: ConditionSummary[] }) {
  const metricNames = [
    ...new Set(conditions.flatMap((condition) => Object.keys(condition.metrics)))
  ].sort();

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Metric</th>
          {conditions.map((condition) => (
            <th key={condition.label}>
              {condition.label}
              {condition.role === "control" && <span className="badge"> control</span>}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>
            <strong>Success rate</strong>
            <div className="muted small">fact — validation verdict</div>
          </td>
          {conditions.map((condition) => (
            <td key={condition.label}>
              {formatPercent(condition.success_rate)}
              <div className="muted small">
                {condition.scored_runs}/{condition.runs} scored
              </div>
            </td>
          ))}
        </tr>
        {metricNames.map((name) => (
          <tr key={name}>
            <td className="mono small">{name}</td>
            {conditions.map((condition) => (
              <td className="mono small" key={condition.label}>
                {spread(condition.metrics[name])}
                {condition.metrics[name]?.missing ? (
                  <div className="muted small">
                    {condition.metrics[name].missing} missing
                  </div>
                ) : null}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Turn a declared design into runs.
 *
 * The arms and repetitions are already fixed by the experiment — this only
 * supplies what every arm shares. Anything the design varies is overridden per
 * arm afterwards, so a model typed here never silently becomes a confound.
 */
/**
 * Turn a declared design into runs — with the run form's own controls.
 *
 * The first version of this panel asked for the model and harness as bare text
 * and hardcoded a score_mode the API refuses, so the one button on it produced
 * a validation error and nothing else. Everything here is now the same
 * machinery the Runs form uses: the three-tier model picker, the case's own
 * prompt variants, the preflight check, and the one score mode the web API
 * accepts.
 *
 * The arms and repetitions stay fixed by the experiment — this only supplies
 * what every arm shares. Anything the design varies is overridden per arm
 * afterwards, so a value picked here never becomes a confound.
 */
function LaunchPanel({
  experimentId,
  onLaunched
}: {
  experimentId: string;
  onLaunched: () => void;
}) {
  const [caseIds, setCaseIds] = useState<string[]>([]);
  // Set once the experiment has real runs: the backend refuses a relaunch
  // that differs, so the panel stops asking and just offers another round.
  const [locked, setLocked] = useState<LaunchConfigSummary | null>(null);
  // Variants pinned by the design itself; when present the select disappears.
  const [designVariants, setDesignVariants] = useState<string[]>([]);
  // Union across the experiment's cases — same rule as batch mode: a case
  // without the chosen variant contributes no runs for it, but an intersection
  // would go empty as soon as one case is unusual.
  const [variantOptions, setVariantOptions] = useState<string[]>([]);
  const [promptVariant, setPromptVariant] = useState("sdlc");
  const [harness, setHarness] = useState("opencode");
  const [plannerModel, setPlannerModel] = useState("");
  const [executorModel, setExecutorModel] = useState("");
  const [gatewayDefault, setGatewayDefault] = useState("");
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [directErrors, setDirectErrors] = useState<Record<string, string>>({});
  const [reloadingModels, setReloadingModels] = useState(false);
  const [maxSteps, setMaxSteps] = useState(10);
  const [stepOptions, setStepOptions] = useState<number[]>([3, 6, 10]);
  const [dryRun, setDryRun] = useState(false);
  const [check, setCheck] = useState<RunValidationResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadModels = (refresh = false) => {
    setReloadingModels(refresh);
    return api
      .listModels(refresh)
      .then((catalog) => {
        setModelOptions(catalog.models);
        setDirectErrors(catalog.direct_errors ?? {});
        if (catalog.default_executor) {
          setGatewayDefault(catalog.default_executor);
          // Adopt the default only while nothing was picked yet, and only for
          // gateway harnesses — never clobber a choice the operator made.
          setExecutorModel((current) =>
            current === "" && !HARNESS_NATIVE_MODELS[harness]
              ? catalog.default_executor
              : current
          );
        }
        return catalog;
      })
      .finally(() => setReloadingModels(false));
  };

  useEffect(() => {
    let active = true;
    void loadModels().catch(() => {
      // Gateway/API offline — the pickers show their empty-state guidance.
    });
    Promise.allSettled([api.getExperiment(experimentId), api.listCases()]).then(
      ([experimentResult, casesResult]) => {
        if (!active) {
          return;
        }
        const ids =
          experimentResult.status === "fulfilled"
            ? experimentResult.value.case_ids
            : [];
        setCaseIds(ids);
        if (experimentResult.status === "fulfilled") {
          setDesignVariants(experimentResult.value.prompt_variants ?? []);
          setLocked(experimentResult.value.launch_config ?? null);
        }
        if (casesResult.status === "fulfilled") {
          const mine = casesResult.value.filter((item) =>
            ids.includes(item.case_id)
          );
          const names = new Set<string>();
          const budgets = new Set<number>([3, 6, 10]);
          for (const item of mine) {
            for (const variant of Object.keys(item.prompt_variants ?? {})) {
              names.add(variant);
            }
            if (item.max_steps) {
              budgets.add(item.max_steps);
            }
          }
          const sortedNames = [...names].sort();
          setVariantOptions(sortedNames);
          // Prefer the un-doctored baseline of whichever family the cases are
          // from: "sdlc" for the lab's own cases, "swe_default" for imported
          // SWE-bench instances. Alphabetical order would land on
          // "swe_conductor", which is a treatment prompt, not a default.
          const preferred = ["sdlc", "swe_default"].find((name) =>
            sortedNames.includes(name)
          );
          if (sortedNames.length > 0) {
            setPromptVariant(preferred ?? sortedNames[0]);
          }
          const sortedBudgets = [...budgets].sort((a, b) => a - b);
          setStepOptions(sortedBudgets);
          // Default to the largest budget any of the cases declares: an
          // experiment mixing a 6-step canary with a 40-step task has to fund
          // the 40-step task, or its arm fails for reasons that are not the
          // treatment.
          setMaxSteps(sortedBudgets[sortedBudgets.length - 1]);
        }
      }
    );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId]);

  // A verdict only describes the configuration that produced it, so any edit
  // voids it rather than leaving a stale green tick above the Launch button.
  useEffect(() => {
    setCheck(null);
  }, [promptVariant, harness, plannerModel, executorModel, maxSteps, dryRun]);

  const handleHarnessChange = (next: string) => {
    setHarness(next);
    const native = HARNESS_NATIVE_MODELS[next];
    setExecutorModel(native ? native[0].id : gatewayDefault);
  };

  const buildConfig = (caseId: string): RunConfigPayload => ({
    case_id: caseId,
    prompt_variant: promptVariant,
    harness,
    planner_model: plannerModel || null,
    executor_model: executorModel || null,
    // The one mode the web API accepts. Runs with objective validation score
    // themselves from it; the rest land in awaiting_manual_score.
    score_mode: "manual",
    max_steps: maxSteps,
    resume: false,
    dry_run: dryRun
  });

  const handleCheck = async () => {
    setChecking(true);
    setError(null);
    setCheck(null);
    try {
      setCheck(await api.preflightRunConfig(buildConfig(caseIds[0] ?? "")));
    } catch (checkError) {
      setError(
        checkError instanceof Error ? checkError.message : "Unable to check"
      );
    } finally {
      setChecking(false);
    }
  };

  const launch = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.launchExperiment(
        experimentId,
        // A locked experiment relaunches with exactly what it already ran —
        // the backend refuses anything else, and asking again in a form is
        // how two measurements end up filed under one name.
        locked
          ? {
              case_id: caseIds[0] ?? "",
              prompt_variant: locked.prompt_variant,
              harness: locked.harness,
              planner_model: locked.planner_model,
              executor_model: locked.executor_model,
              score_mode: "manual",
              max_steps: locked.max_steps,
              resume: false,
              dry_run: false,
              // Dropping these turns a subject-validation relaunch into a
              // config mismatch the backend refuses.
              skills_disabled: locked.skills_disabled ?? [],
              skills_extra: locked.skills_extra ?? [],
              skills_invoke: locked.skills_invoke ?? []
            }
          : buildConfig(caseIds[0] ?? "")
      );
      setNotice(
        `Queued ${result.count} run(s) — every case in every arm, repeated as ` +
          "the design declares. Results fill in here as they finish."
      );
      onLaunched();
    } catch (launchError) {
      setError(
        launchError instanceof Error ? launchError.message : "Unable to launch"
      );
    } finally {
      setBusy(false);
    }
  };

  if (locked) {
    return (
      <div className="card">
        <h3>Run this experiment</h3>
        <p className="muted">
          Launched and locked: every further round reuses the configuration the
          existing runs already carry, so one experiment stays one measurement.
          Want a different model or budget? That is a different measurement —
          create a new experiment.
        </p>
        <div className="detail-grid">
          <div>
            <span>Harness</span>
            <strong>{locked.harness}</strong>
          </div>
          <div>
            <span>Executor model</span>
            <strong>{locked.executor_model ?? "harness default"}</strong>
          </div>
          <div>
            <span>Planner model</span>
            <strong>{locked.planner_model ?? "none"}</strong>
          </div>
          <div>
            <span>Prompt variant</span>
            <strong>{locked.prompt_variant}</strong>
          </div>
          <div>
            <span>Step budget</span>
            <strong>{locked.max_steps}</strong>
          </div>
          {locked.skills_extra?.length ? (
            <div>
              <span>Skills added</span>
              <strong>{locked.skills_extra.join(", ")}</strong>
            </div>
          ) : null}
          {locked.skills_invoke?.length ? (
            <div>
              <span>Skills asked for</span>
              <strong>{locked.skills_invoke.join(", ")}</strong>
            </div>
          ) : null}
          {locked.skills_disabled?.length ? (
            <div>
              <span>Skills removed</span>
              <strong>{locked.skills_disabled.join(", ")}</strong>
            </div>
          ) : null}
        </div>
        {error ? (
          <ErrorBanner message={error} onDismiss={() => setError(null)} />
        ) : null}
        {notice ? (
          <p className="callout callout--ok">
            {notice} <Link to="/advanced/runs">Watch them in Runs</Link>.
          </p>
        ) : null}
        <div className="button-row">
          <button
            className="primary"
            disabled={busy}
            onClick={() => void launch()}
            type="button"
          >
            {busy ? "Queueing…" : "Queue another round — same configuration"}
          </button>
        </div>
      </div>
    );
  }

  if (caseIds.length === 0) {
    return (
      <div className="card">
        <h3>Run this experiment</h3>
        <p className="callout callout--warning">
          This experiment has no cases, so there is nothing to launch. Create a
          new one from <Link to="/evaluate/new">New evaluation</Link> or from the
          repository&apos;s workbench; experiments are immutable once declared.
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Run this experiment</h3>
      <p className="muted">
        This experiment is a draft — it has no real runs yet. Shared settings for
        every arm; the design supplies the rest, and a field the experiment
        varies is overridden per arm, so nothing picked here can leak into the
        comparison as a confound.
      </p>

      <div className="field-row">
        <label className="field">
          <span>Harness</span>
          <select
            onChange={(event) => handleHarnessChange(event.target.value)}
            value={harness}
          >
            {HARNESS_OPTIONS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          {HOST_HARNESSES.has(harness) ? (
            <span className="muted small">
              Runs the {harness} CLI with its native auth.{" "}
              {harness === "claude"
                ? "Works in Docker (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or host mode (make app-host)."
                : "Needs host mode — run make app-host."}
            </span>
          ) : null}
        </label>
        {designVariants.length > 0 ? (
          <div className="field">
            <span>Prompt variants</span>
            <p className="muted small">
              Fixed by the design: {designVariants.join(", ")} — every arm runs
              each of them.
            </p>
          </div>
        ) : (
          <label className="field">
            <span>Prompt variant</span>
            {variantOptions.length > 0 ? (
              <select
                onChange={(event) => setPromptVariant(event.target.value)}
                value={promptVariant}
              >
                {variantOptions.map((variant) => (
                  <option key={variant} value={variant}>
                    {variant}
                  </option>
                ))}
              </select>
            ) : (
              <input
                onChange={(event) => setPromptVariant(event.target.value)}
                value={promptVariant}
              />
            )}
            <span className="muted small">
              Union of the variants the experiment&apos;s cases define. A case
              without the chosen variant contributes no runs for it.
            </span>
          </label>
        )}
        <label className="field">
          <span>Step budget</span>
          <select
            onChange={(event) => setMaxSteps(Number(event.target.value))}
            value={maxSteps}
          >
            {stepOptions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <span className="muted small">
            Defaults to the largest budget any selected case declares.
          </span>
        </label>
      </div>

      <div className="field-row">
        <ModelPicker
          label="Executor model"
          directErrors={directErrors}
          nativeOptions={HARNESS_NATIVE_MODELS[harness]}
          onChange={setExecutorModel}
          onReload={() => void loadModels(true)}
          options={modelOptions}
          reloading={reloadingModels}
          value={executorModel}
        >
          <small className="muted">
            {HARNESS_NATIVE_MODELS[harness]
              ? `Native ${harness} CLI models (passed to --model).`
              : "Held fixed across every arm — the experiment varies the harness, never the model."}
          </small>
        </ModelPicker>
        <ModelPicker
          allowNone
          label="Planner model"
          directErrors={directErrors}
          onChange={setPlannerModel}
          options={modelOptions}
          value={plannerModel}
        />
      </div>

      <label className="checkbox">
        <input
          checked={dryRun}
          onChange={(event) => setDryRun(event.target.checked)}
          type="checkbox"
        />
        <span>Dry run — build the commands without executing them</span>
      </label>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {check ? (
        <p className={`callout ${check.ok ? "callout--ok" : "callout--warning"}`}>
          <strong>{check.ok ? "Ready" : "Blocked"}</strong> — {check.detail}
        </p>
      ) : null}
      {notice ? (
        <p className="callout callout--ok">
          {notice} <Link to="/advanced/runs">Watch them in Runs</Link>.
        </p>
      ) : null}

      <div className="button-row">
        <button
          className="secondary"
          disabled={checking || busy}
          onClick={() => void handleCheck()}
          type="button"
        >
          {checking ? "Checking…" : "Check configuration"}
        </button>
        <button
          className="primary"
          disabled={busy}
          onClick={() => void launch()}
          type="button"
        >
          {busy ? "Queueing…" : "Launch runs"}
        </button>
      </div>
    </div>
  );
}

function ExperimentDetail({
  summary,
  record,
  onLaunched,
  onAbandon,
  onDelete
}: {
  summary: ExperimentSummary;
  record?: ExperimentRecord;
  onLaunched: () => void;
  onAbandon: (experimentId: string) => void;
  onDelete: (experimentId: string, runCount: number) => void;
}) {
  const runCount = record?.run_count ?? 0;
  return (
    <div className="stack">
      {summary.validity ? <ValidityBanner validity={summary.validity} /> : null}
      {summary.verdict ? <VerdictCard verdict={summary.verdict} /> : null}
      <div className="card">
        <div className="row space-between">
          <h2>{summary.question}</h2>
          <div className="button-row">
            {record && record.status !== "abandoned" ? (
              <button
                className="secondary"
                onClick={() => onAbandon(summary.experiment_id)}
                title="Hide from the list. Its runs stay in the corpus — this is 'stop showing me this', not 'this never happened'."
                type="button"
              >
                Abandon
              </button>
            ) : null}
            <button
              className="danger"
              onClick={() => onDelete(summary.experiment_id, runCount)}
              title={
                runCount > 0
                  ? `Erase this experiment and its ${runCount} run(s) — artifacts and scores leave every dashboard.`
                  : "Erase this draft."
              }
              type="button"
            >
              Delete
            </button>
          </div>
        </div>
        <p className="muted">
          {MODE_LABEL[summary.mode] ?? summary.mode} · {summary.repetitions} repetition
          {summary.repetitions === 1 ? "" : "s"} per arm
        </p>
        <p className="muted small">{MODE_CAVEAT[summary.mode]}</p>
        {summary.subject ? (
          <p className="muted">
            Subject under test: <strong>{summary.subject}</strong>
          </p>
        ) : null}
        {summary.conditions.flatMap((condition) => condition.warnings).length > 0 && (
          <ul className="warning-list">
            {[
              ...new Set(summary.conditions.flatMap((condition) => condition.warnings))
            ].map((warning) => (
              <li key={warning}>
                <IconAlert size={14} /> {warning}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <h3>Conditions</h3>
        <ConditionTable conditions={summary.conditions} />
      </div>

      <LaunchPanel experimentId={summary.experiment_id} onLaunched={onLaunched} />

      {summary.mode === "subject_validation" && summary.case_outcomes ? (
        <CaseOutcomeGrid outcomes={summary.case_outcomes} />
      ) : null}

      {summary.comparisons.map((comparison) => (
        <div className="card" key={`${comparison.control}-${comparison.treatment}`}>
          <h3>
            {comparison.treatment} vs {comparison.control}
          </h3>
          <p className="muted">
            Success rate {formatPercent(comparison.success.control)} →{" "}
            {formatPercent(comparison.success.treatment)}
            {comparison.success.points !== null && (
              <>
                {" "}
                (<strong>{comparison.success.points > 0 ? "+" : ""}
                {comparison.success.points.toFixed(0)} pp</strong>)
              </>
            )}
          </p>

          <table className="data-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>{comparison.control}</th>
                <th>{comparison.treatment}</th>
                <th>Δ</th>
                <th>Beyond noise?</th>
              </tr>
            </thead>
            <tbody>
              {comparison.deltas.map((delta) => (
                <tr key={delta.name}>
                  <td className="mono small">{delta.name}</td>
                  <td className="mono small">{formatNumber(delta.control)}</td>
                  <td className="mono small">{formatNumber(delta.treatment)}</td>
                  <td className={`mono small ${deltaClass(delta)}`}>
                    {deltaLabel(delta)}
                  </td>
                  <td className="small">
                    {delta.exceeds_spread === null
                      ? "unknown"
                      : delta.exceeds_spread
                        ? "yes"
                        : "no — within run-to-run variance"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4>Case matrix</h4>
          <p className="muted small">
            Where the treatment helps, and where it does not. An average of "+8
            points" can be "fixed four cases, broke two" — only this says which.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>{comparison.control}</th>
                <th>{comparison.treatment}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {comparison.case_matrix.map((row) => (
                <tr key={row.case_id}>
                  <td className="mono small">{row.case_id}</td>
                  <td>
                    <Outcome value={row.control} />
                  </td>
                  <td>
                    <Outcome value={row.treatment} />
                  </td>
                  <td className="small">
                    <span className={MOVEMENT_CLASS[row.movement]}>{row.movement}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

export function Experiments() {
  const [params, setParams] = useSearchParams();
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([]);
  // Deep-linkable: the wizard lands here on the experiment it just created.
  const [selected, setSelected] = useState<string | null>(params.get("experiment"));
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Bumped after a launch so the summary refetches with the queued runs in it.
  const [refresh, setRefresh] = useState(0);
  // Abandoned experiments are clutter by definition — opt in to see them.
  const [showAbandoned, setShowAbandoned] = useState(false);

  useEffect(() => {
    api
      .listExperiments()
      .then((rows) => {
        setExperiments(rows);
        setError(null);
      })
      .catch((loadError: unknown) =>
        setError(
          loadError instanceof Error ? loadError.message : "Unable to load experiments"
        )
      )
      .finally(() => setIsLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (selected === null) {
      setSummary(null);
      return;
    }
    api
      .summarizeExperiment(selected)
      .then(setSummary)
      .catch((summaryError: unknown) =>
        setError(
          summaryError instanceof Error
            ? summaryError.message
            : "Unable to summarize experiment"
        )
      );
  }, [selected, refresh]);

  const handleAbandon = async (experimentId: string) => {
    try {
      await api.abandonExperiment(experimentId);
      setRefresh((value) => value + 1);
    } catch (abandonError) {
      setError(
        abandonError instanceof Error ? abandonError.message : "Unable to abandon"
      );
    }
  };

  const handleDelete = async (experimentId: string, runCount: number) => {
    const message =
      runCount > 0
        ? `Delete "${experimentId}" AND its ${runCount} run(s)? Their artifacts ` +
          "and scores disappear from every dashboard and aggregate. This cannot " +
          "be undone."
        : `Delete the draft "${experimentId}"? This cannot be undone.`;
    if (!window.confirm(message)) {
      return;
    }
    try {
      await api.deleteExperiment(experimentId, runCount > 0);
      setSelected(null);
      setParams({});
      setRefresh((value) => value + 1);
    } catch (deleteError) {
      setError(
        deleteError instanceof Error ? deleteError.message : "Unable to delete"
      );
    }
  };

  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <h1>Experiments</h1>
          <p className="muted">
            A stated question, declared arms, and the runs that answer it.
          </p>
        </div>
      </header>

      <HelpPanel title="What an experiment is, and what a batch is not">
        <p>
          An experiment states its <strong>question</strong> before anything runs, and
          declares its arms up front — a control and its treatments, or, for subject
          validation, peer arms with no control. The lab
          checks the design against the mode and refuses one that cannot answer the
          question it claims to: a comparison that varies both the harness and the
          repository produces a number that cannot be attributed to either.
        </p>
        <p className="muted">
          A batch is still the right tool for an ad-hoc sweep. The difference is that
          an experiment makes a claim, so it has to earn it.
        </p>
      </HelpPanel>

      {error !== null && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="card">
        <div className="row space-between">
          <h2>All experiments</h2>
          {experiments.some((item) => item.status === "abandoned") ? (
            <label className="row gap">
              <input
                checked={showAbandoned}
                onChange={(event) => setShowAbandoned(event.target.checked)}
                type="checkbox"
              />
              <span>
                Show abandoned (
                {experiments.filter((item) => item.status === "abandoned").length})
              </span>
            </label>
          ) : null}
        </div>
        {isLoading ? (
          <Spinner />
        ) : experiments.length === 0 ? (
          <EmptyState
            action={
              <div className="empty-state-buttons">
                <Link className="primary" to="/evaluate/new?mode=harness_evaluation">
                  Evaluate a harness change
                </Link>
                <Link className="secondary" to="/evaluate/audit">
                  Audit a repository
                </Link>
                <Link className="secondary" to="/evaluate/new?mode=cross_repo">
                  Cross-repo evaluation
                </Link>
              </div>
            }
            description="Start by choosing what you want to learn. An experiment needs a question, a mode, cases and at least one arm."
            title="No experiments yet."
          />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Id</th>
                <th>Question</th>
                <th>Mode</th>
                <th>Arms</th>
                <th>Runs</th>
                <th>Reps</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {experiments
                .filter(
                  (experiment) =>
                    showAbandoned || experiment.status !== "abandoned"
                )
                .map((experiment) => (
                <tr key={experiment.experiment_id}>
                  <td>
                    <strong>{experiment.experiment_id}</strong>
                    <div className="muted small">{experiment.status}</div>
                  </td>
                  <td className="small">{experiment.question}</td>
                  <td className="small">
                    {MODE_LABEL[experiment.mode] ?? experiment.mode}
                  </td>
                  <td>{experiment.condition_count ?? "—"}</td>
                  <td>{experiment.run_count ?? "—"}</td>
                  <td>
                    {experiment.repetitions}
                    {experiment.repetitions < 5 && (
                      <div className="muted small">exploratory</div>
                    )}
                  </td>
                  <td>
                    <button
                      onClick={() => {
                        const next =
                          selected === experiment.experiment_id
                            ? null
                            : experiment.experiment_id;
                        setSelected(next);
                        setParams(next ? { experiment: next } : {});
                      }}
                      type="button"
                    >
                      {selected === experiment.experiment_id ? "Hide" : "Open"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {summary !== null && (
        <ExperimentDetail
          onAbandon={(id) => void handleAbandon(id)}
          onDelete={(id, runCount) => void handleDelete(id, runCount)}
          onLaunched={() => setRefresh((value) => value + 1)}
          record={experiments.find(
            (item) => item.experiment_id === summary.experiment_id
          )}
          summary={summary}
        />
      )}
    </section>
  );
}
