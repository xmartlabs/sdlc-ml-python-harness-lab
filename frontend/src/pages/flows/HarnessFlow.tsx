import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api";
import { GuideFlow } from "../../components/guide/GuideFlow";
import type { GuideStep, GuideValues } from "../../components/guide/GuideFlow";
import {
  armFor,
  estimateCells,
  IS_3D,
  loadCases,
  loadModels,
  modelOptions
} from "../../components/guide/guideData";
import { caseOptions } from "./ModelsFlow";
import { defineMessages, useMessages } from "../../i18n";
import type { CaseRecord, ModelOption, SkillProfileRecord } from "../../types";

/**
 * Harness flow (ADR-0093): one case, one model, two or more harness levels —
 * no harness, minimal, full. Creates a ladder: one clean `harness_evaluation`
 * A/B per adjacent pair of the chosen levels, in one family
 * (`POST /api/experiments/harness-levels`). One experiment holding all three
 * levels would move two harness factors at once; Harness × model, which needs
 * two or more models, keeps its own screen.
 */

type Level = "bare" | "minimal" | "full";
/** Ladder order: the A/Bs are the adjacent pairs of the chosen levels. */
const LEVELS: readonly Level[] = ["bare", "minimal", "full"];

const MESSAGES = defineMessages({
  en: {
    title: "Harness",
    nodes: { case: "Case", model: "Model", levels: "Harness levels", reps: "Repetitions" },
    caseQ: "Which case?",
    modelQ: "Which model?",
    levelsQ: "Does this case do better with no harness, minimal, or full?",
    levelsHint: "Pick at least 2. Each runs the same case, the same model, nothing else changed.",
    repsQ: "How many times each?",
    local: "local · this machine",
    cloud: "cloud",
    bareLabel: "No harness",
    minimalLabel: (n: number) => `Minimal · ${n} skills`,
    fullLabel: (n: number) => `Full · ${n} skills`,
    bareTip: "Empty workspace, the lab's governance masked — the prompt and nothing else.",
    minimalTip: "Rules, hooks, and the small set of skills kept in the template by default.",
    fullTip: "Rules, hooks, and every governed skill available.",
    note3d:
      "No 3D case has ever run without a harness: this would be the first data point. Each 3D run takes 30 to 75 minutes — start with 1 repetition.",
    outcome: "One verdict per level: what each harness buys this case, on this model.",
    question: (caseId: string) => `Does ${caseId} need a harness?`,
    noteRungs: (n: number) =>
      n === 1
        ? "One A/B: the two levels you picked, only the level moves."
        : `${n} A/Bs in one family, one per step up (none → minimal → full): only the level moves in each.`
  },
  es: {
    title: "Arnés",
    nodes: { case: "Caso", model: "Modelo", levels: "Niveles de arnés", reps: "Repeticiones" },
    caseQ: "¿En qué caso?",
    modelQ: "¿Qué modelo?",
    levelsQ: "¿Este caso rinde mejor sin arnés, con minimal o con full?",
    levelsHint: "Elegí al menos 2. Cada uno corre el mismo caso, el mismo modelo, nada más cambia.",
    repsQ: "¿Cuántas veces cada uno?",
    local: "local · esta máquina",
    cloud: "nube",
    bareLabel: "Sin arnés",
    minimalLabel: (n: number) => `Minimal · ${n} skills`,
    fullLabel: (n: number) => `Full · ${n} skills`,
    bareTip: "Workspace vacío, la gobernanza del lab oculta — el prompt y nada más.",
    minimalTip: "Reglas, hooks y el conjunto chico de skills que el template mantiene por defecto.",
    fullTip: "Reglas, hooks y todas las skills gobernadas disponibles.",
    note3d:
      "Nunca se corrió un caso 3D sin arnés: este sería el primer dato. Cada corrida 3D tarda de 30 a 75 minutos: arrancá con 1 repetición.",
    outcome: "Un veredicto por nivel: qué compra cada arnés en este caso, con este modelo.",
    question: (caseId: string) => `¿${caseId} necesita arnés?`,
    noteRungs: (n: number) =>
      n === 1
        ? "Un A/B: los dos niveles que elegiste, solo cambia el nivel."
        : `${n} A/B en una familia, uno por escalón (sin arnés → minimal → full): en cada uno solo cambia el nivel.`
  }
});


export function HarnessFlow() {
  const m = useMessages(MESSAGES);
  // `?case=` from the showcase ("And without harness?") starts with the case chosen.
  const [params] = useSearchParams();
  const initial: GuideValues = params.get("case") ? { case: params.get("case") ?? "" } : {};
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [profiles, setProfiles] = useState<Record<string, SkillProfileRecord>>({});

  useEffect(() => {
    loadCases().then(setCases);
    loadModels().then(setModels);
    api.listSkillProfiles().then(setProfiles).catch(() => setProfiles({}));
  }, []);

  const levelOptions = () => [
    { id: "bare", label: m.bareLabel, tip: m.bareTip },
    { id: "minimal", label: m.minimalLabel(profiles.minimal?.skills.length ?? 0), tip: m.minimalTip },
    { id: "full", label: m.fullLabel(profiles.full?.skills.length ?? 0), tip: m.fullTip }
  ];

  const steps: GuideStep[] = [
    { key: "case", node: m.nodes.case, question: m.caseQ, kind: "one", options: () => caseOptions(cases) },
    { key: "model", node: m.nodes.model, question: m.modelQ, kind: "one", options: () => modelOptions(models, m.local, m.cloud) },
    {
      key: "levels",
      node: m.nodes.levels,
      question: m.levelsQ,
      hint: m.levelsHint,
      kind: "many",
      min: 2,
      options: levelOptions
    },
    { key: "reps", node: m.nodes.reps, question: m.repsQ, kind: "reps" }
  ];


  return (
    <GuideFlow
      initial={initial}
      estimate={(values) => {
        const caseId = String(values.case);
        const modelId = String(values.model);
        const levels = (values.levels as string[]) ?? [];
        const reps = Number(values.reps ?? 0);
        // The ladder is one A/B per adjacent pair of the chosen levels: 3 levels
        // are 2 rungs = 4 arms (the middle level runs in both).
        const rungs = Math.max(0, LEVELS.filter((level) => levels.includes(level)).length - 1);
        const notes = [m.noteRungs(rungs)];
        if (IS_3D(caseId) && levels.includes("bare")) notes.push(m.note3d);
        return estimateCells([{ caseId, modelId, runs: 2 * rungs * reps }], 2 * rungs, notes);
      }}
      onCreate={async (values) => {
        // One clean A/B per adjacent level pair, in one family (ADR-0093):
        // a single three-arm experiment would move two harness factors at once.
        const arm = armFor(String(values.model));
        const created = await api.createHarnessLevels({
          case_id: String(values.case),
          model: arm.model,
          harness: arm.harness,
          levels: (values.levels as string[]) ?? [],
          repetitions: Number(values.reps)
        });
        return {
          ids: created.experiments.map((row) => row.experiment_id),
          warnings: created.experiments.flatMap((row) => row.warnings ?? [])
        };
      }}
      cli={(values) =>
        // No make target builds this ladder (`make ladder` climbs governance,
        // skills and prompt instead), so show the call that does.
        `curl -X POST localhost:8008/api/experiments/harness-levels \\\n  -H 'content-type: application/json' \\\n  -d '${JSON.stringify({
          case_id: String(values.case),
          model: armFor(String(values.model)).model,
          harness: armFor(String(values.model)).harness,
          levels: values.levels,
          repetitions: Number(values.reps)
        })}'`
      }
      outcome={m.outcome}
      steps={steps}
      title={m.title}
    />
  );
}
