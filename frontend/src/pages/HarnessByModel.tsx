import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { IconArrowRight, IconLayers } from "../components/icons";
import { useNativeModels } from "../harnesses";
import type { CaseRecord } from "../types";

/**
 * Do stronger models need less harness? (ADR-0066)
 *
 * A deliberately small form: pick cases and models, and the lab builds the full
 * harness x model grid itself — every harness level on every model — because
 * an incomplete grid cannot separate the interaction from a confound and the
 * backend refuses it. The harness factor is `harness_source`, the one axis
 * whose levels need nothing else configured: no harness (the runner masks the
 * governance) versus this lab's harness.
 */

type Level = {
  key: string;
  label: string;
  note: string;
  overrides: (skills: string[]) => Record<string, unknown>;
};

// A ladder, each rung adding one thing to the one below (ADR-0051, ADR-0066):
// none → governance (rules, hooks) → governance + skills. The middle rung is
// the "partial harness": everything except the skills, so its gap to the top
// rung is what the skills add, and its gap to the bottom what governance adds.
const NONE: Level = {
  key: "none",
  label: "No harness",
  note: "governance masked in the runner",
  overrides: () => ({ harness_source: "bare" })
};
const GOVERNANCE: Level = {
  key: "governance",
  label: "Governance only",
  note: "rules and hooks, every skill disabled",
  overrides: (skills) => ({ harness_source: "lab_root", skills_disabled: skills })
};
const FULL: Level = {
  key: "full",
  label: "Full harness",
  note: "rules, hooks and skills",
  overrides: () => ({ harness_source: "lab_root" })
};

const DEFAULT_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"];
const MAX_SLUG = 60;

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, MAX_SLUG) || "harness-by-model"
  );
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

export function HarnessByModel() {
  const navigate = useNavigate();
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>(DEFAULT_MODELS);
  const [withGovernance, setWithGovernance] = useState(true);
  const [skills, setSkills] = useState<string[]>([]);
  useEffect(() => {
    api
      .listHarnessSkills()
      .then((rows) => setSkills(rows.map((row) => row.name)))
      .catch(() => setSkills([]));
  }, []);
  // The middle rung needs skills to disable; without them it would equal the top.
  const LEVELS: Level[] =
    withGovernance && skills.length > 0 ? [NONE, GOVERNANCE, FULL] : [NONE, FULL];
  // Smallest to largest within each generation, current generation first: the
  // interaction is read from the first model declared to the last (ADR-0066).
  const catalog = useNativeModels();
  const MODELS = [...(catalog.claude ?? [])].sort(
    (a, b) =>
      (a.generation === b.generation ? 0 : a.generation === "current" ? -1 : 1) ||
      a.size_rank - b.size_rank
  );
  const [repetitions, setRepetitions] = useState(3);
  const [question, setQuestion] = useState("Do stronger models need less harness?");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .listCases()
      .then((rows) => setCases(rows.filter((row) => row.status !== "archived")))
      .catch((loadError: unknown) =>
        setError(loadError instanceof Error ? loadError.message : "Unable to load cases")
      );
  }, []);

  // Variants every selected case declares — the launch needs one they all have.
  const sharedVariant = useMemo(() => {
    const selected = cases.filter((row) => caseIds.includes(row.case_id));
    if (selected.length === 0) return null;
    const sets = selected.map((row) => new Set(Object.keys(row.prompt_variants ?? {})));
    const shared = [...sets[0]].filter((variant) => sets.every((set) => set.has(variant)));
    return shared.includes("default") ? "default" : (shared[0] ?? null);
  }, [cases, caseIds]);

  // The grid is ordered as it is declared: models in the order picked from the
  // list (smallest first by default), so the interaction runs first → last.
  const orderedModels = MODELS.map((model) => model.id).filter((id) => models.includes(id));
  // The step budget of the most demanding selected case: one fixed number
  // under-funds a long case and locks that budget for every later launch.
  const maxSteps = Math.max(
    6,
    ...cases.filter((row) => caseIds.includes(row.case_id)).map((row) => row.max_steps ?? 6)
  );
  // Mixing generations can reverse the interaction's direction: say so.
  const generations = new Set(
    MODELS.filter((model) => models.includes(model.id)).map((model) => model.generation)
  );
  // A model the gateway has no card for fails at launch when runs go through it.
  const offGateway = MODELS.filter(
    (model) => models.includes(model.id) && model.on_gateway === false
  ).map((model) => model.id);
  const cells = LEVELS.length * orderedModels.length;
  const runs = cells * caseIds.length * repetitions;

  const problems: string[] = [];
  if (caseIds.length === 0) problems.push("Pick at least one case.");
  if (caseIds.length > 0 && caseIds.length < 3)
    problems.push(
      "With fewer than three cases the interaction is never called established — the cases are the unit of evidence."
    );
  if (orderedModels.length < 2) problems.push("Pick at least two models.");
  if (offGateway.length)
    problems.push(
      `Your gateway has no card for ${offGateway.join(", ")}. Runs routed through it will fail at launch — add the card to ai-gateway, or run with a Claude credential directly.`
    );
  if (generations.size > 1)
    problems.push(
      "You mixed current and previous-generation models. The order is smallest first within each generation, so the interaction no longer reads 'smaller to larger' — compare one generation at a time."
    );
  if (caseIds.length > 0 && !sharedVariant)
    problems.push("The selected cases share no prompt variant; pick cases from one family.");
  const blocking = caseIds.length === 0 || orderedModels.length < 2 || !sharedVariant;

  const create = async (launch: boolean) => {
    if (blocking || !sharedVariant) return;
    setSaving(true);
    setError(null);
    // The same question asked twice is a second experiment, not a collision: take
    // the first free id rather than failing on the one already used.
    const base = `${slugify(question)}-${orderedModels.length}x${LEVELS.length}`;
    const taken = new Set(
      await api
        .listExperiments()
        .then((rows) => rows.map((row) => row.experiment_id))
        .catch(() => [] as string[])
    );
    let experimentId = base;
    for (let counter = 2; taken.has(experimentId) && counter < 100; counter += 1) {
      experimentId = `${base.slice(0, MAX_SLUG - 4)}-${counter}`;
    }
    const conditions = orderedModels.flatMap((model, modelIndex) =>
      LEVELS.map((level, levelIndex) => ({
        label: `${level.key}-${model}`,
        // One arm carries the control role so the record is well formed; the
        // factorial reading does not use it (ADR-0066).
        role: modelIndex === 0 && levelIndex === 0 ? "control" : "treatment",
        // `harness` is fixed in the design, not only in the launch payload, so a
        // later launch from the Experiments page runs the same CLI.
        overrides: { harness: "claude", ...level.overrides(skills), executor_model: model }
      }))
    );
    let created: { experiment_id: string };
    try {
      created = await api.createExperiment({
        experiment_id: experimentId,
        name: question.slice(0, 80),
        question,
        mode: "harness_by_model",
        case_ids: caseIds,
        prompt_variants: [sharedVariant],
        repetitions,
        conditions
      });
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to create the experiment");
      setSaving(false);
      return;
    }
    const page = `/evaluate/experiments?experiment=${created.experiment_id}`;
    if (launch) {
      try {
        await api.launchExperiment(created.experiment_id, {
          case_id: caseIds[0],
          prompt_variant: sharedVariant,
          harness: "claude",
          planner_model: null,
          executor_model: null,
          score_mode: "manual",
          max_steps: maxSteps,
          resume: false,
          dry_run: false
        });
      } catch (launchError) {
        // The experiment exists: retrying here would create a second one. Send
        // the user to it, where the launch can be retried on the same design.
        setSaving(false);
        navigate(page, {
          state: {
            launchError: `Created '${created.experiment_id}', but the launch was refused: ${
              launchError instanceof Error ? launchError.message : "unknown error"
            }. Launch it from this page.`
          }
        });
        return;
      }
    }
    setSaving(false);
    navigate(page);
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">
            <IconLayers size={14} /> Harness x model
          </span>
          <h1>Do stronger models need less harness?</h1>
          <p className="lede">
            The lab runs every harness level on every model, then reports the harness
            effect per model and how it changes from the first model to the last — with
            the interval that says whether the change is real.
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} /> : null}

      <section className="panel">
        <h2>1. Cases</h2>
        <p className="muted small">
          Pick cases where the smallest model does not already pass everything without a
          harness — a case every arm passes cannot show an effect.
        </p>
        <div className="checkbox-stack">
          {cases.map((row) => (
            <label className="checkbox" key={row.case_id}>
              <input
                checked={caseIds.includes(row.case_id)}
                onChange={() => setCaseIds((current) => toggle(current, row.case_id))}
                type="checkbox"
              />
              <span>
                <strong>{row.name}</strong>{" "}
                <span className="muted small mono">{row.case_id}</span>
              </span>
            </label>
          ))}
          {cases.length === 0 ? (
            <p className="muted small">
              No cases yet. <Link to="/evidence/cases">Create or import one</Link>.
            </p>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <h2>2. Models, smallest first</h2>
        <div className="checkbox-stack">
          {MODELS.map((model) => (
            <label className="checkbox" key={model.id}>
              <input
                checked={models.includes(model.id)}
                onChange={() => setModels((current) => toggle(current, model.id))}
                type="checkbox"
              />
              <span>
                {model.label} <span className="muted small mono">{model.id}</span>
              </span>
            </label>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>3. Design</h2>
        <div className="form-grid">
          <label className="full">
            Question
            <input onChange={(event) => setQuestion(event.target.value)} value={question} />
          </label>
          <label>
            Repetitions per cell and case
            <select
              onChange={(event) => setRepetitions(Number(event.target.value))}
              value={repetitions}
            >
              {[2, 3, 5].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <div>
            <span className="muted small">Harness levels, smallest first</span>
            <ul className="onboarding-steps">
              {LEVELS.map((level) => (
                <li key={level.key}>
                  <strong>{level.label}</strong> — {level.note}
                </li>
              ))}
            </ul>
            <label className="checkbox">
              <input
                checked={withGovernance}
                disabled={skills.length === 0}
                onChange={(event) => setWithGovernance(event.target.checked)}
                type="checkbox"
              />
              <span>
                Add a partial rung (governance without skills) to separate what the
                rules add from what the skills add.
              </span>
            </label>
          </div>
        </div>
        <p className="muted small">
          {cells} cells ({LEVELS.length} harness levels x {orderedModels.length} models) x{" "}
          {caseIds.length} case(s) x {repetitions} repetitions = <strong>{runs} runs</strong>
          {sharedVariant ? ` · prompt variant '${sharedVariant}'` : ""}. Runs execute one at a
          time; check cost on the first cell before waiting for all of them.
        </p>
        {problems.length > 0 ? (
          <ul className="muted small">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}
        <div className="panel-actions">
          <button
            className="secondary"
            disabled={blocking || saving}
            onClick={() => create(false)}
            type="button"
          >
            Create without launching
          </button>
          <button
            className="primary"
            disabled={blocking || saving}
            onClick={() => create(true)}
            type="button"
          >
            Create and launch {runs} runs
            <IconArrowRight size={15} />
          </button>
        </div>
      </section>
    </section>
  );
}
