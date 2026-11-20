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
  modelOptions,
  slugify,
  uniqueExperimentId
} from "../../components/guide/guideData";
import { defineMessages, useMessages } from "../../i18n";
import type { CaseRecord, ModelOption } from "../../types";

/**
 * Models flow (ADR-0093): one case, two or more models, the same prompt and
 * repetitions. Creates exactly what Compare models creates — a
 * `subject_validation` experiment in the case's `models-` family, every model a
 * peer, each paired with the harness that drives it — so its results land next
 * to every earlier comparison on that case.
 */

const MESSAGES = defineMessages({
  en: {
    title: "Models",
    nodes: { case: "Case", models: "Models", reps: "Repetitions" },
    caseQ: "Which case?",
    modelsQ: "Which models do you compare?",
    modelsHint: "Pick 2 or more. Each runs with the harness that drives it.",
    repsQ: "How many times each?",
    local: "local · this machine",
    cloud: "cloud",
    outcome: "A table per model: checks per phase, cost and time. On a 3D case, the Play tab.",
    note3d: "Each 3D run uses the Godot image (make images-3d) and takes 30 to 75 minutes.",
    question: (caseId: string) => `Which model builds ${caseId} best?`
  },
  es: {
    title: "Modelos",
    nodes: { case: "Caso", models: "Modelos", reps: "Repeticiones" },
    caseQ: "¿En qué caso?",
    modelsQ: "¿Qué modelos comparás?",
    modelsHint: "Elegí 2 o más. Cada uno corre con el arnés que lo maneja.",
    repsQ: "¿Cuántas veces cada uno?",
    local: "local · esta máquina",
    cloud: "nube",
    outcome: "Una tabla por modelo: chequeos por fase, costo y tiempo. En un caso 3D, la pestaña Jugar.",
    note3d: "Cada corrida 3D usa la imagen de Godot (make images-3d) y tarda de 30 a 75 minutos.",
    question: (caseId: string) => `¿Qué modelo construye mejor ${caseId}?`
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

  useEffect(() => {
    loadCases().then(setCases);
    loadModels().then(setModels);
  }, []);

  const initial: GuideValues = params.get("case") ? { case: params.get("case") ?? "" } : {};

  const steps: GuideStep[] = [
    { key: "case", node: m.nodes.case, question: m.caseQ, kind: "one", options: () => caseOptions(cases) },
    {
      key: "models",
      node: m.nodes.models,
      question: m.modelsQ,
      hint: m.modelsHint,
      kind: "many",
      min: 2,
      options: () => modelOptions(models, m.local, m.cloud)
    },
    { key: "reps", node: m.nodes.reps, question: m.repsQ, kind: "reps" }
  ];

  const variantFor = (caseId: string) => {
    const row = cases.find((item) => item.case_id === caseId);
    const variants = Object.keys(row?.prompt_variants ?? {});
    return variants.includes("default") ? "default" : (variants[0] ?? "default");
  };

  return (
    <GuideFlow
      estimate={(values) => {
        const caseId = String(values.case);
        const chosen = (values.models as string[]) ?? [];
        const reps = Number(values.reps ?? 0);
        return estimateCells(
          chosen.map((modelId) => ({ caseId, modelId, runs: reps })),
          chosen.length,
          IS_3D(caseId) ? [m.note3d] : []
        );
      }}
      initial={initial}
      onCreate={async (values) => {
        const caseId = String(values.case);
        const chosen = (values.models as string[]) ?? [];
        const arms = chosen.map(armFor);
        const labels = arms.map((arm) => slugify(arm.model.split("/").pop() ?? arm.model).replace(/^claude-/, ""));
        const unique = labels.map((label, i) => (labels.indexOf(label) === i ? label : `${label}-${i + 1}`));
        const experimentId = await uniqueExperimentId(`${caseId}-models-${unique.join("-vs-")}`.slice(0, 60));
        const created = await api.createExperiment({
          experiment_id: experimentId,
          name: m.question(caseId).slice(0, 80),
          question: m.question(caseId),
          mode: "subject_validation",
          subject: `model comparison on ${caseId}`,
          case_ids: [caseId],
          prompt_variants: [variantFor(caseId)],
          repetitions: Number(values.reps),
          family_id: `models-${caseId}`,
          conditions: arms.map((arm, i) => ({
            label: unique[i],
            role: "treatment",
            overrides: { harness: arm.harness, executor_model: arm.model }
          }))
        });
        return { ids: [created.experiment_id] };
      }}
      outcome={m.outcome}
      steps={steps}
      title={m.title}
    />
  );
}
