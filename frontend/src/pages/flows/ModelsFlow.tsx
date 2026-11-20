import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api";
import { GuideFlow, SKIPPED } from "../../components/guide/GuideFlow";
import type { GuideStep, GuideValues } from "../../components/guide/GuideFlow";
import {
  armFor,
  defaultVariant,
  estimateCells,
  IS_3D,
  launchBlocker,
  launchExperiments,
  levelOverrides,
  loadCases,
  loadClis,
  loadModels,
  loadNative,
  slugify,
  uniqueExperimentId
} from "../../components/guide/guideData";
import { armLabel, HarnessModelChooser } from "../../components/guide/HarnessModelChooser";
import { defineMessages, useMessages } from "../../i18n";
import type { HarnessLevel } from "../../components/guide/guideData";
import type { CaseRecord, ModelOption, NativeModelRecord, SkillProfileRecord } from "../../types";

/**
 * Models flow (ADR-0093): one case, one or two models, the same prompt and
 * repetitions. Each model is picked the same way — the agent CLI first, then one
 * of its models — so a pair is explicit: opencode on a local Qwen against
 * claude on Opus, or codex against gemini. One model alone is a valid answer:
 * "how does this new model do here?" is read against the case's earlier runs.
 * Creates exactly what Compare models creates — a `subject_validation`
 * experiment in the case's `models-` family, every arm a peer — and launches it
 * from the review, so its results land next to every earlier run of that case.
 * Three or four models: Compare models.
 *
 * Every arm runs at one harness level, chosen here and pinned in each arm:
 * full (the default, and what every earlier comparison ran), minimal, or none.
 *
 * Since 2026-11-20 this is also the harness flow ("Models and harness", one
 * card): the first question is what varies. "Models" is the comparison above;
 * "Harness levels" keeps one model and builds the ladder — one clean
 * `harness_evaluation` A/B per adjacent pair of the chosen levels
 * (`POST /api/experiments/harness-levels`), the only design that reads a
 * harness difference with a verdict. `/flows/harness` lands here with it chosen.
 */

const MESSAGES = defineMessages({
  en: {
    title: "Models",
    nodes: {
      vary: "Compare",
      case: "Case",
      first: "Model 1",
      second: "Model 2",
      model: "Model",
      level: "Harness",
      levels: "Harness levels",
      reps: "Repetitions"
    },
    varyQ: "What do you compare?",
    varyModels: "Models",
    varyModelsSub: "one or two models, the same harness",
    varyModelsTip:
      "Which model does this case best, at what cost? One model alone is a probe. Every arm runs at one harness level you pick.",
    varyLevels: "Harness levels",
    varyLevelsSub: "one model: none → minimal → full",
    varyLevelsTip:
      "Does this model need the harness here, and how much? One clean A/B per step up, each with a verdict. Works on every case, 3D included.",
    modelQ: "Which agent and model?",
    levelsQ: "Which levels do you compare?",
    levelsHint: "Pick at least 2. Same case, same model, nothing else changes.",
    noteRungs: (n: number) =>
      n === 1
        ? "One A/B: the two levels you picked, only the level moves."
        : `${n} A/Bs in one family, one per step up (none → minimal → full): only the level moves in each.`,
    noteBare3d:
      "This may be the first 3D run without a harness. Each 3D run takes 30 to 75 minutes: start with 1 repetition.",
    outcomeLevels: "One verdict per step up: what each harness level buys this case, on this model.",
    caseQ: "Which case?",
    firstQ: "First model: which agent, and which of its models?",
    secondQ: "Compare it with a second model?",
    onlyOne: "No, just this model",
    onlyOneHint: "Cheaper: one arm. Its runs sit in the case's models family, next to every earlier model tried on it.",
    none: "none",
    armHint: "The tab is the agent CLI that runs the model; under it, only the models it can drive.",
    levelQ: "With how much harness?",
    levelHint: "The same level for every model. To compare levels on one model, use the Harness flow.",
    fullLabel: (n: number) => `Full · ${n} skills`,
    fullSub: "the default — comparable with earlier runs",
    fullTip: "Rules, hooks and every governed skill. What every earlier model comparison ran, so the results line up with them.",
    minimalLabel: (n: number) => `Minimal · ${n} skills`,
    minimalSub: "the template's default set",
    minimalTip: "Rules, hooks and the small set of skills the template keeps by default.",
    bareLabel: "No harness",
    bareSub: "the prompt and nothing else",
    bareTip: "Empty workspace, the lab's governance masked: no rules, hooks or skills. How the model does on its own.",
    levelNote: (level: string) =>
      `Harness: ${level}. Runs at another level are a different population; read them apart.`,
    repsQ: "How many times each?",
    parallel: "Two at a time",
    parallelHint: "Runs the two models side by side on the second lane. Halves the wait; each 3D run takes 4 GB. Full harness only: minimal and none rewrite the shared skill tree.",
    outcome: "A table per model: checks per phase, cost and time. On a 3D case, the Play tab.",
    note3d: "Each 3D run uses the Godot image (make images-3d) and takes 30 to 75 minutes.",
    question: (caseId: string) => `Which model builds ${caseId} best?`,
    questionOne: (model: string, caseId: string) => `How does ${model} do on ${caseId}?`,
    atLevel: (question: string, level: string) => (level === "full" ? question : `${question} (${level === "bare" ? "no harness" : level})`)
  },
  es: {
    title: "Modelos",
    nodes: {
      vary: "Comparar",
      case: "Caso",
      first: "Modelo 1",
      second: "Modelo 2",
      model: "Modelo",
      level: "Arnés",
      levels: "Niveles de arnés",
      reps: "Repeticiones"
    },
    varyQ: "¿Qué comparás?",
    varyModels: "Modelos",
    varyModelsSub: "uno o dos modelos, el mismo arnés",
    varyModelsTip:
      "¿Qué modelo resuelve mejor este caso, a qué costo? Un solo modelo es una prueba. Todos los brazos corren con el nivel de arnés que elijas.",
    varyLevels: "Niveles de arnés",
    varyLevelsSub: "un modelo: sin → minimal → full",
    varyLevelsTip:
      "¿Este modelo necesita el arnés acá, y cuánto? Un A/B limpio por escalón, cada uno con veredicto. Funciona en todos los casos, también los 3D.",
    modelQ: "¿Qué agente y modelo?",
    levelsQ: "¿Qué niveles comparás?",
    levelsHint: "Elegí al menos 2. Mismo caso, mismo modelo, nada más cambia.",
    noteRungs: (n: number) =>
      n === 1
        ? "Un A/B: los dos niveles que elegiste, solo cambia el nivel."
        : `${n} A/B en una familia, uno por escalón (sin arnés → minimal → full): en cada uno solo cambia el nivel.`,
    noteBare3d:
      "Puede ser la primera corrida 3D sin arnés. Cada corrida 3D tarda de 30 a 75 minutos: arrancá con 1 repetición.",
    outcomeLevels: "Un veredicto por escalón: qué compra cada nivel de arnés en este caso, con este modelo.",
    caseQ: "¿En qué caso?",
    firstQ: "Primer modelo: ¿qué agente, y cuál de sus modelos?",
    secondQ: "¿Lo comparás con un segundo modelo?",
    onlyOne: "No, solo este modelo",
    onlyOneHint: "Más barato: un solo brazo. Sus corridas quedan en la familia de modelos del caso, junto a todos los modelos que ya se probaron ahí.",
    none: "ninguno",
    armHint: "La pestaña es la CLI de agente que corre el modelo; abajo, solo los modelos que puede manejar.",
    levelQ: "¿Con cuánto arnés?",
    levelHint: "El mismo nivel para todos los modelos. Para comparar niveles en un modelo, usá el flujo Arnés.",
    fullLabel: (n: number) => `Full · ${n} skills`,
    fullSub: "por defecto — comparable con corridas anteriores",
    fullTip: "Reglas, hooks y todas las skills gobernadas. Lo que corrió cada comparación de modelos anterior, así los resultados se pueden poner al lado.",
    minimalLabel: (n: number) => `Minimal · ${n} skills`,
    minimalSub: "el conjunto por defecto del template",
    minimalTip: "Reglas, hooks y el conjunto chico de skills que el template mantiene por defecto.",
    bareLabel: "Sin arnés",
    bareSub: "el prompt y nada más",
    bareTip: "Workspace vacío, la gobernanza del lab oculta: sin reglas, hooks ni skills. Cómo le va al modelo solo.",
    levelNote: (level: string) =>
      `Arnés: ${level}. Las corridas con otro nivel son otra población; leelas por separado.`,
    repsQ: "¿Cuántas veces cada uno?",
    parallel: "De a dos a la vez",
    parallelHint: "Corre los dos modelos en paralelo en el segundo carril. La espera se reduce a la mitad; cada corrida 3D usa 4 GB. Solo con arnés full: minimal y sin arnés reescriben el árbol de skills compartido.",
    outcome: "Una tabla por modelo: chequeos por fase, costo y tiempo. En un caso 3D, la pestaña Jugar.",
    note3d: "Cada corrida 3D usa la imagen de Godot (make images-3d) y tarda de 30 a 75 minutos.",
    question: (caseId: string) => `¿Qué modelo construye mejor ${caseId}?`,
    questionOne: (model: string, caseId: string) => `¿Cómo le va a ${model} en ${caseId}?`,
    atLevel: (question: string, level: string) => (level === "full" ? question : `${question} (${level === "bare" ? "sin arnés" : level})`)
  }
});

export function caseOptions(cases: CaseRecord[]) {
  return cases.map((row) => ({
    id: row.case_id,
    label: row.name && row.name !== row.case_id && row.name.length < 40 ? row.name : row.case_id,
    sub: (row.tags ?? []).slice(0, 3).join(" · "),
    tip: row.description ? row.description.slice(0, 280) : row.case_id
  }));
}

export function ModelsFlow() {
  const m = useMessages(MESSAGES);
  const [params] = useSearchParams();
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [native, setNative] = useState<Record<string, NativeModelRecord[]>>({});
  const [laneUp, setLaneUp] = useState(false);
  const [profiles, setProfiles] = useState<Record<string, SkillProfileRecord>>({});

  useEffect(() => {
    loadCases().then(setCases);
    loadModels().then(setModels);
    loadNative().then(setNative);
    void loadClis();
    api.listSkillProfiles().then(setProfiles).catch(() => setProfiles({}));
    api
      .getRunLanes()
      .then((lanes) => setLaneUp(lanes.parallel === true))
      .catch(() => setLaneUp(false));
  }, []);

  const initial: GuideValues = {};
  if (params.get("vary") === "levels" || params.get("vary") === "models") initial.vary = params.get("vary") ?? "";
  if (params.get("case")) initial.case = params.get("case") ?? "";
  const display = (value: string) => (value === SKIPPED ? m.none : armLabel(value, models, native));
  const isLadder = (values: GuideValues) => values.vary === "levels";
  const notLadder = (values: GuideValues) => !isLadder(values);

  const levelCards = () => [
    { id: "full", label: m.fullLabel(profiles.full?.skills.length ?? 0), sub: m.fullSub, tip: m.fullTip },
    { id: "minimal", label: m.minimalLabel(profiles.minimal?.skills.length ?? 0), sub: m.minimalSub, tip: m.minimalTip },
    { id: "bare", label: m.bareLabel, sub: m.bareSub, tip: m.bareTip }
  ];

  const steps: GuideStep[] = [
    {
      key: "vary",
      node: m.nodes.vary,
      question: m.varyQ,
      kind: "one",
      options: () => [
        { id: "models", label: m.varyModels, sub: m.varyModelsSub, tip: m.varyModelsTip },
        { id: "levels", label: m.varyLevels, sub: m.varyLevelsSub, tip: m.varyLevelsTip }
      ]
    },
    { key: "case", node: m.nodes.case, question: m.caseQ, kind: "one", options: () => caseOptions(cases) },
    // The ladder: one model, then which levels.
    {
      key: "model",
      node: m.nodes.model,
      question: m.modelQ,
      hint: m.armHint,
      kind: "custom",
      skip: notLadder,
      display,
      render: (values, pick) => <HarnessModelChooser onPick={pick} value={String(values.model ?? "")} />
    },
    {
      key: "levels",
      node: m.nodes.levels,
      question: m.levelsQ,
      hint: m.levelsHint,
      kind: "many",
      min: 2,
      skip: notLadder,
      // Ladder order, none first: the A/Bs are the adjacent pairs.
      options: () => [...levelCards()].reverse()
    },
    // The comparison: one or two models, one level.
    {
      key: "first",
      skip: isLadder,
      node: m.nodes.first,
      question: m.firstQ,
      hint: m.armHint,
      kind: "custom",
      display,
      render: (values, pick) => <HarnessModelChooser onPick={pick} value={String(values.first ?? "")} />
    },
    {
      key: "second",
      skip: isLadder,
      node: m.nodes.second,
      question: m.secondQ,
      hint: m.armHint,
      kind: "custom",
      display,
      skippable: { label: m.onlyOne, hint: m.onlyOneHint },
      render: (values, pick) => (
        <HarnessModelChooser
          exclude={[String(values.first ?? "")]}
          onPick={pick}
          value={String(values.second ?? "")}
        />
      )
    },
    {
      key: "level",
      node: m.nodes.level,
      question: m.levelQ,
      hint: m.levelHint,
      kind: "one",
      skip: isLadder,
      options: levelCards
    },
    { key: "reps", node: m.nodes.reps, question: m.repsQ, kind: "reps" }
  ];

  const levelOf = (values: GuideValues): HarnessLevel =>
    values.level === "bare" || values.level === "minimal" ? values.level : "full";
  // Two lanes only for two arms at full: a model comparison's arms may fix
  // nothing but harness and model (ADR-0082), and an ablation rewrites the
  // shared skill tree, so the server refuses it in parallel.
  const canParallel = (values: GuideValues) =>
    !isLadder(values) && laneUp && chosenOf(values).length > 1 && levelOf(values) === "full";

  /** The ladder's A/Bs: adjacent pairs of the chosen levels (3 levels = 2 rungs). */
  const rungsOf = (values: GuideValues) => {
    const chosen = (values.levels as string[]) ?? [];
    return Math.max(0, ["bare", "minimal", "full"].filter((level) => chosen.includes(level)).length - 1);
  };

  const chosenOf = (values: GuideValues) =>
    [String(values.first ?? ""), String(values.second ?? "")].filter((value) => value && value !== SKIPPED);

  return (
    <GuideFlow
      estimate={async (values) => {
        const caseId = String(values.case);
        const caseRow = cases.find((row) => row.case_id === caseId);
        if (isLadder(values)) {
          const modelId = String(values.model);
          const rungs = rungsOf(values);
          const levels = (values.levels as string[]) ?? [];
          const notes = [m.noteRungs(rungs)];
          if (IS_3D(caseId) && levels.includes("bare")) notes.push(m.noteBare3d);
          const [cells, blocker] = await Promise.all([
            // Priced per level: a bare run is not a full run.
            estimateCells(
              levels.map((level) => ({
                caseId,
                modelId,
                // The middle level runs in both rungs.
                runs: Number(values.reps ?? 0) * (level === "minimal" && rungs === 2 ? 2 : 1),
                level
              })),
              2 * rungs,
              notes
            ),
            launchBlocker(caseRow, [modelId])
          ]);
          return { ...cells, blocker };
        }
        const chosen = chosenOf(values);
        const reps = Number(values.reps ?? 0);
        const level = levelOf(values);
        const notes = level === "full" ? [] : [m.levelNote(level === "bare" ? m.bareLabel : "minimal")];
        if (IS_3D(caseId)) notes.push(m.note3d);
        const [cells, blocker] = await Promise.all([
          estimateCells(
            chosen.map((modelId) => ({ caseId, modelId, runs: reps, level })),
            chosen.length,
            notes
          ),
          launchBlocker(
            cases.find((row) => row.case_id === caseId),
            chosen,
            level === "full" ? {} : levelOverrides(level, profiles.minimal?.disabled ?? [])
          )
        ]);
        return { ...cells, blocker };
      }}
      initial={initial}
      onCreate={async (values) => {
        const caseId = String(values.case);
        if (isLadder(values)) {
          // One clean A/B per adjacent level pair, in one family (ADR-0093):
          // one experiment holding all three would move two factors at once.
          const arm = armFor(String(values.model));
          const created = await api.createHarnessLevels({
            case_id: caseId,
            model: arm.model,
            harness: arm.harness,
            effort: arm.effort,
            levels: (values.levels as string[]) ?? [],
            repetitions: Number(values.reps)
          });
          return {
            ids: created.experiments.map((row) => row.experiment_id),
            warnings: created.experiments.flatMap((row) => row.warnings ?? [])
          };
        }
        const arms = chosenOf(values).map(armFor);
        const tails = arms.map(
          (arm) =>
            slugify(arm.model.split("/").pop() ?? arm.model).replace(/^claude-/, "") +
            (arm.effort ? `-${arm.effort}` : "")
        );
        // The same model through two agents (opencode vs claude on Opus) is
        // told apart by its agent, not by a number.
        const labels = tails.map((tail, i) => (tails.indexOf(tail) === tails.lastIndexOf(tail) ? tail : `${arms[i].harness}-${tail}`));
        const unique = labels.map((label, i) => (labels.indexOf(label) === i ? label : `${label}-${i + 1}`));
        const experimentId = await uniqueExperimentId(`${caseId}-models-${unique.join("-vs-")}`.slice(0, 60));
        // One model is a probe, not a race: the question says so.
        const level = levelOf(values);
        const question = m.atLevel(
          arms.length === 1 ? m.questionOne(arms[0].model, caseId) : m.question(caseId),
          level
        );
        // Full adds nothing to an arm: it is what every earlier comparison ran,
        // and the arm stays the plain harness + model shape two lanes accept.
        const pinned = level === "full" ? {} : levelOverrides(level, profiles.minimal?.disabled ?? []);
        const created = await api.createExperiment({
          experiment_id: experimentId,
          name: question.slice(0, 80),
          question,
          mode: "subject_validation",
          subject: arms.length === 1 ? `${arms[0].model} on ${caseId}` : `model comparison on ${caseId}`,
          notes: `Harness level: ${level} (ADR-0093, amended 2026-11-20).`,
          case_ids: [caseId],
          prompt_variants: [defaultVariant(cases.find((row) => row.case_id === caseId))],
          repetitions: Number(values.reps),
          family_id: `models-${caseId}`,
          conditions: arms.map((arm, i) => ({
            label: unique[i],
            role: "treatment",
            overrides: {
              harness: arm.harness,
              executor_model: arm.model,
              ...(arm.effort ? { executor_effort: arm.effort } : {}),
              ...pinned
            }
          }))
        });
        return { ids: [created.experiment_id] };
      }}
      onLaunch={(values, created) =>
        launchExperiments(
          created.ids,
          cases.find((row) => row.case_id === String(values.case)),
          String(isLadder(values) ? values.model : values.first),
          canParallel(values) && values.parallel === "1" ? 2 : 1
        )
      }
      reviewExtra={(values, set) =>
        canParallel(values) ? (
          <label className="checkbox">
            <input
              checked={values.parallel === "1"}
              onChange={(event) => set("parallel", event.target.checked ? "1" : "0")}
              type="checkbox"
            />
            <span>
              {m.parallel} <span className="muted small">{m.parallelHint}</span>
            </span>
          </label>
        ) : null
      }
      outcome={m.outcome}
      outcomeFor={(values) => (isLadder(values) ? m.outcomeLevels : m.outcome)}
      cli={(values) =>
        isLadder(values)
          ? `curl -X POST localhost:8009/api/experiments/harness-levels \\\n  -H 'content-type: application/json' \\\n  -d '${JSON.stringify({
              case_id: String(values.case),
              model: armFor(String(values.model)).model,
              harness: armFor(String(values.model)).harness,
              effort: armFor(String(values.model)).effort || undefined,
              levels: values.levels,
              repetitions: Number(values.reps)
            })}'`
          : ""
      }
      steps={steps}
      title={m.title}
    />
  );
}
