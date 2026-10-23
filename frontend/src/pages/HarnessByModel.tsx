import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { IconArrowRight, IconLayers } from "../components/icons";
import { useNativeModels } from "../harnesses";
import { defineMessages, pickMessages, useMessages } from "../i18n";
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
  overrides: (skills: string[]) => Record<string, unknown>;
};

// A ladder, each rung adding one thing to the one below (ADR-0051, ADR-0066):
// none → governance (rules, hooks) → governance + skills. The middle rung is
// the "partial harness": everything except the skills, so its gap to the top
// rung is what the skills add, and its gap to the bottom what governance adds.
// The display label and note for each key live in MESSAGES, read at render time.
const NONE: Level = {
  key: "none",
  overrides: () => ({ harness_source: "bare" })
};
const GOVERNANCE: Level = {
  key: "governance",
  overrides: (skills) => ({ harness_source: "lab_root", skills_disabled: skills })
};
const FULL: Level = {
  key: "full",
  overrides: () => ({ harness_source: "lab_root" })
};

const DEFAULT_MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"];
const MAX_SLUG = 60;

const QUESTION = "Do stronger models need less harness?";

const MESSAGES = defineMessages({
  en: {
    eyebrow: "Harness x model",
    title: QUESTION,
    question: QUESTION,
    lede: "The lab runs every harness level on every model, then reports the harness effect per model and how it changes from the first model to the last — with the interval that says whether the change is real.",
    levelLabel: {
      none: "No harness",
      governance: "Governance only",
      full: "Full harness"
    } as Record<string, string>,
    levelNote: {
      none: "governance masked in the runner",
      governance: "rules and hooks, every skill disabled",
      full: "rules, hooks and skills"
    } as Record<string, string>,
    casesTitle: "1. Cases",
    casesHint:
      "Pick cases where the smallest model does not already pass everything without a harness — a case every arm passes cannot show an effect.",
    noCasesYet: (link: ReactNode) => <>No cases yet. {link}.</>,
    createOrImportOne: "Create or import one",
    modelsTitle: "2. Models, smallest first",
    designTitle: "3. Design",
    questionLabel: "Question",
    repetitionsLabel: "Repetitions per cell and case",
    harnessLevelsSmallestFirst: "Harness levels, smallest first",
    addPartialRung:
      "Add a partial rung (governance without skills) to separate what the rules add from what the skills add.",
    cellsSummary: (
      cells: number,
      levels: number,
      models: number,
      cases: number,
      repetitions: number,
      runs: number,
      variantSuffix: string
    ) => (
      <>
        {cells} cells ({levels} harness levels x {models} models) x{" "}
        {cases} case(s) x {repetitions} repetitions ={" "}
        <strong>{runs} runs</strong>
        {variantSuffix}. Runs execute one at a time; check cost on the first
        cell before waiting for all of them.
      </>
    ),
    variantSuffix: (variant: string) => ` · prompt variant '${variant}'`,
    pickAtLeastOneCase: "Pick at least one case.",
    fewerThanThreeCases:
      "With fewer than three cases the interaction is never called established — the cases are the unit of evidence.",
    pickAtLeastTwoModels: "Pick at least two models.",
    noGatewayCard: (models: string) =>
      `Your gateway has no card for ${models}. Runs routed through it will fail at launch — add the card to ai-gateway, or run with a Claude credential directly.`,
    mixedGenerations:
      "You mixed current and previous-generation models. The order is smallest first within each generation, so the interaction no longer reads 'smaller to larger' — compare one generation at a time.",
    noSharedVariant: "The selected cases share no prompt variant; pick cases from one family.",
    createWithoutLaunching: "Create without launching",
    createAndLaunch: (runs: number) => `Create and launch ${runs} runs`,
    unableToLoadCases: "Unable to load cases",
    unknownError: "unknown error",
    launchRefused: (experimentId: string, reason: string) =>
      `Created '${experimentId}', but the launch was refused: ${reason}. Launch it from this page.`,
    unableToCreate: "Unable to create the experiment"
  },
  es: {
    eyebrow: "Arnés x modelo",
    title: "¿Los modelos más fuertes necesitan menos arnés?",
    question: "¿Los modelos más fuertes necesitan menos arnés?",
    lede: "El lab corre cada nivel de arnés en cada modelo, y después reporta el efecto del arnés por modelo y cómo cambia del primer modelo al último — con el intervalo que dice si el cambio es real.",
    levelLabel: {
      none: "Sin arnés",
      governance: "Solo gobernanza",
      full: "Arnés completo"
    } as Record<string, string>,
    levelNote: {
      none: "gobernanza oculta en el runner",
      governance: "reglas y hooks, todas las skills deshabilitadas",
      full: "reglas, hooks y skills"
    } as Record<string, string>,
    casesTitle: "1. Casos",
    casesHint:
      "Elegí casos donde el modelo más chico no pase ya todo sin arnés — un caso que todo brazo pasa no puede mostrar un efecto.",
    noCasesYet: (link: ReactNode) => <>Todavía no hay casos. {link}.</>,
    createOrImportOne: "Creá o importá uno",
    modelsTitle: "2. Modelos, de menor a mayor",
    designTitle: "3. Diseño",
    questionLabel: "Pregunta",
    repetitionsLabel: "Repeticiones por celda y caso",
    harnessLevelsSmallestFirst: "Niveles de arnés, de menor a mayor",
    addPartialRung:
      "Agregá un escalón parcial (gobernanza sin skills) para separar lo que suman las reglas de lo que suman las skills.",
    cellsSummary: (
      cells: number,
      levels: number,
      models: number,
      cases: number,
      repetitions: number,
      runs: number,
      variantSuffix: string
    ) => (
      <>
        {cells} celdas ({levels} niveles de arnés x {models} modelos) x{" "}
        {cases} caso(s) x {repetitions} repeticiones ={" "}
        <strong>{runs} corridas</strong>
        {variantSuffix}. Las corridas se ejecutan de a una; revisá el costo en
        la primera celda antes de esperar a todas.
      </>
    ),
    variantSuffix: (variant: string) => ` · variante de prompt '${variant}'`,
    pickAtLeastOneCase: "Elegí al menos un caso.",
    fewerThanThreeCases:
      "Con menos de tres casos la interacción nunca se llama establecida — los casos son la unidad de evidencia.",
    pickAtLeastTwoModels: "Elegí al menos dos modelos.",
    noGatewayCard: (models: string) =>
      `Tu gateway no tiene carta para ${models}. Las corridas ruteadas por ahí van a fallar al lanzar — agregá la carta en ai-gateway, o corré con una credencial de Claude directa.`,
    mixedGenerations:
      "Mezclaste modelos de la generación actual y de una anterior. El orden es de menor a mayor dentro de cada generación, así que la interacción ya no se lee 'de menor a mayor' — comparalas de a una generación por vez.",
    noSharedVariant:
      "Los casos elegidos no comparten ninguna variante de prompt; elegí casos de una sola familia.",
    createWithoutLaunching: "Crear sin lanzar",
    createAndLaunch: (runs: number) => `Crear y lanzar ${runs} corridas`,
    unableToLoadCases: "No se pudieron cargar los casos",
    unknownError: "error desconocido",
    launchRefused: (experimentId: string, reason: string) =>
      `Se creó '${experimentId}', pero el lanzamiento fue rechazado: ${reason}. Lanzalo desde esta página.`,
    unableToCreate: "No se pudo crear el experimento"
  }
});

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
  const m = useMessages(MESSAGES);
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
  // Sent to the API as the experiment's declared question: kept in English,
  // like any other value the backend stores, until the user edits it.
  const [question, setQuestion] = useState(QUESTION);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .listCases()
      .then((rows) => setCases(rows.filter((row) => row.status !== "archived")))
      .catch((loadError: unknown) =>
        setError(loadError instanceof Error ? loadError.message : m.unableToLoadCases)
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  if (caseIds.length === 0) problems.push(m.pickAtLeastOneCase);
  if (caseIds.length > 0 && caseIds.length < 3) problems.push(m.fewerThanThreeCases);
  if (orderedModels.length < 2) problems.push(m.pickAtLeastTwoModels);
  if (offGateway.length) problems.push(m.noGatewayCard(offGateway.join(", ")));
  if (generations.size > 1) problems.push(m.mixedGenerations);
  if (caseIds.length > 0 && !sharedVariant) problems.push(m.noSharedVariant);
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
      setError(saveError instanceof Error ? saveError.message : m.unableToCreate);
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
            launchError: m.launchRefused(
              created.experiment_id,
              launchError instanceof Error ? launchError.message : m.unknownError
            )
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
            <IconLayers size={14} /> {m.eyebrow}
          </span>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} /> : null}

      <section className="panel">
        <h2>{m.casesTitle}</h2>
        <p className="muted small">{m.casesHint}</p>
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
              {m.noCasesYet(
                <Link to="/evidence/cases">{m.createOrImportOne}</Link>
              )}
            </p>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <h2>{m.modelsTitle}</h2>
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
        <h2>{m.designTitle}</h2>
        <div className="form-grid">
          <label className="full">
            {m.questionLabel}
            <input onChange={(event) => setQuestion(event.target.value)} value={question} />
          </label>
          <label>
            {m.repetitionsLabel}
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
            <span className="muted small">{m.harnessLevelsSmallestFirst}</span>
            <ul className="onboarding-steps">
              {LEVELS.map((level) => (
                <li key={level.key}>
                  <strong>{m.levelLabel[level.key]}</strong> — {m.levelNote[level.key]}
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
              <span>{m.addPartialRung}</span>
            </label>
          </div>
        </div>
        <p className="muted small">
          {m.cellsSummary(
            cells,
            LEVELS.length,
            orderedModels.length,
            caseIds.length,
            repetitions,
            runs,
            sharedVariant ? m.variantSuffix(sharedVariant) : ""
          )}
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
            {m.createWithoutLaunching}
          </button>
          <button
            className="primary"
            disabled={blocking || saving}
            onClick={() => create(true)}
            type="button"
          >
            {m.createAndLaunch(runs)}
            <IconArrowRight size={15} />
          </button>
        </div>
      </section>
    </section>
  );
}
