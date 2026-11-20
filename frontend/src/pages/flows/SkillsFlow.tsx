import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api";
import { GuideFlow } from "../../components/guide/GuideFlow";
import type { GuideOption, GuideStep, GuideValues } from "../../components/guide/GuideFlow";
import { armFor, estimateCells, loadCases, loadModels, modelOptions } from "../../components/guide/guideData";
import { caseOptions } from "./ModelsFlow";
import { defineMessages, useMessages } from "../../i18n";
import type { CaseRecord, ModelOption, SkillProfileRecord, StagedSkillRecord } from "../../types";

/**
 * Skills flow (ADR-0093): does a skill earn a place on minimal, full, or
 * both? Creates exactly what `createSkillTrialOnBase` creates — the backend
 * reuses an identical A/B already measured rather than re-running it.
 */

type CaseCandidate = { name: string; caseId: string; staged: boolean };

const MESSAGES = defineMessages({
  en: {
    title: "Skills",
    nodes: {
      source: "Source",
      skill: "Skill",
      base: "Base",
      case: "Case",
      model: "Model",
      reps: "Repetitions"
    },
    sourceQ: "Where does this skill come from?",
    sourceCase: "The case's own",
    sourceCatalog: "From the catalog",
    sourceImport: "From another repo",
    sourceWrite: "Written by hand",
    importWriteTip: "Import or write it in Skills → Import, then pick it here.",
    skillQ: "Which skill?",
    skillHint: "",
    stagedHint: "Import or write it in Skills → Import, then pick it here.",
    staged: "staged",
    notStaged: "not staged",
    baseQ: "Does this skill earn a place — on minimal, on full, or both?",
    baseMinimal: "Minimal — does it belong in the template?",
    baseFull: "Full — on top of everything",
    baseBoth: "Both — where does it help?",
    caseQ: "Which case?",
    modelQ: "Which model?",
    repsQ: "How many times each?",
    local: "local · this machine",
    cloud: "cloud",
    reuseLabel: "Reuse an identical A/B already measured",
    reuseHint:
      "Skips re-running a base already measured with the same skill, case and model.",
    noteReuse:
      "If an identical A/B was already measured, it is reused and only the other base runs.",
    outcome: "One verdict per base: read skill_invoked first, then the case's checks, then the delta.",
    question: (skill: string) => `Does ${skill} earn a place in the harness?`
  },
  es: {
    title: "Skills",
    nodes: {
      source: "Origen",
      skill: "Skill",
      base: "Base",
      case: "Caso",
      model: "Modelo",
      reps: "Repeticiones"
    },
    sourceQ: "¿De dónde viene esta skill?",
    sourceCase: "La propia del caso",
    sourceCatalog: "Del catálogo",
    sourceImport: "De otro repo",
    sourceWrite: "Escrita a mano",
    importWriteTip: "Importala o escribila en Skills → Import, y después elegila acá.",
    skillQ: "¿Qué skill?",
    skillHint: "",
    stagedHint: "Importala o escribila en Skills → Import, y después elegila acá.",
    staged: "staged",
    notStaged: "no staged",
    baseQ: "¿Esta skill se gana un lugar: en minimal, en full o en las dos?",
    baseMinimal: "Minimal — ¿entra al template?",
    baseFull: "Full — encima de todo el catálogo",
    baseBoth: "Las dos — ¿dónde aporta?",
    caseQ: "¿En qué caso?",
    modelQ: "¿Qué modelo?",
    repsQ: "¿Cuántas veces cada uno?",
    local: "local · esta máquina",
    cloud: "nube",
    reuseLabel: "Reusar un A/B idéntico ya medido",
    reuseHint:
      "Evita re-correr una base ya medida con la misma skill, caso y modelo.",
    noteReuse:
      "Si ya hay un A/B idéntico medido, se reusa y solo corre la otra base.",
    outcome: "Un veredicto por base: leé primero skill_invoked, después los chequeos del caso, después el delta.",
    question: (skill: string) => `¿${skill} se gana un lugar en el arnés?`
  }
});

export function SkillsFlow() {
  const m = useMessages(MESSAGES);
  const [params] = useSearchParams();
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [profiles, setProfiles] = useState<Record<string, SkillProfileRecord>>({});
  const [staged, setStaged] = useState<StagedSkillRecord[]>([]);
  const [governed, setGoverned] = useState<{ name: string; description: string }[]>([]);
  const [candidates, setCandidates] = useState<CaseCandidate[]>([]);

  useEffect(() => {
    loadCases().then(setCases);
    loadModels().then(setModels);
    api.listSkillProfiles().then(setProfiles).catch(() => setProfiles({}));
    api
      .listStagedSkills()
      .then((rows) => setStaged(rows.filter((row) => row.status !== "archived")))
      .catch(() => setStaged([]));
    api.listHarnessSkills().then(setGoverned).catch(() => setGoverned([]));
  }, []);

  useEffect(() => {
    let active = true;
    loadCases().then((rows) => {
      Promise.all(
        rows.map((row) =>
          api
            .listCaseCandidateSkills(row.case_id)
            .then((entries) => entries.map((entry) => ({ name: entry.name, caseId: row.case_id, staged: entry.staged })))
            .catch(() => [] as CaseCandidate[])
        )
      ).then((lists) => {
        if (active) setCandidates(lists.flat());
      });
    });
    return () => {
      active = false;
    };
  }, []);

  const initial: GuideValues = {};
  const qSkill = params.get("skill");
  const qSource = params.get("source");
  const qCase = params.get("case");
  if (qSkill) initial.skill = qSkill;
  if (qSource) initial.source = qSource;
  if (qCase) initial.case = qCase;

  const candidateFor = (skill: string) => candidates.find((c) => c.name === skill);

  const sourceOptions = (): GuideOption[] => [
    { id: "case", label: m.sourceCase },
    { id: "catalog", label: m.sourceCatalog },
    { id: "import", label: m.sourceImport, tip: m.importWriteTip },
    { id: "write", label: m.sourceWrite, tip: m.importWriteTip }
  ];

  const skillOptions = (values: GuideValues): GuideOption[] => {
    const source = String(values.source ?? "");
    if (source === "case") {
      return candidates.map((c) => ({
        id: c.name,
        label: c.name,
        sub: c.caseId,
        tag: c.staged ? { tone: "ok" as const, text: m.staged } : { tone: "warn" as const, text: m.notStaged }
      }));
    }
    if (source === "catalog") {
      const minimalSkills = new Set(profiles.minimal?.skills ?? []);
      return governed
        .filter((skill) => !minimalSkills.has(skill.name))
        .map((skill) => ({ id: skill.name, label: skill.name, sub: skill.description.slice(0, 80) }));
    }
    // import / write: staged skills, hint points at Skills -> Import.
    return staged.map((skill) => ({ id: skill.name, label: skill.name, sub: skill.description.slice(0, 80) }));
  };

  const baseOptions = (values: GuideValues): GuideOption[] => {
    const source = String(values.source ?? "");
    if (source === "catalog") {
      return [{ id: "minimal", label: m.baseMinimal }];
    }
    return [
      { id: "minimal", label: m.baseMinimal },
      { id: "full", label: m.baseFull },
      { id: "both", label: m.baseBoth }
    ];
  };

  const steps: GuideStep[] = [
    { key: "source", node: m.nodes.source, question: m.sourceQ, kind: "one", options: sourceOptions },
    { key: "skill", node: m.nodes.skill, question: m.skillQ, hint: m.stagedHint, kind: "one", options: skillOptions },
    { key: "base", node: m.nodes.base, question: m.baseQ, kind: "one", options: baseOptions },
    { key: "case", node: m.nodes.case, question: m.caseQ, kind: "one", options: () => caseOptions(cases) },
    { key: "model", node: m.nodes.model, question: m.modelQ, kind: "one", options: () => modelOptions(models, m.local, m.cloud) },
    { key: "reps", node: m.nodes.reps, question: m.repsQ, kind: "reps" }
  ];

  return (
    <GuideFlow
      estimate={(values) => {
        const caseId = String(values.case);
        const modelId = String(values.model);
        const base = String(values.base ?? "minimal");
        const reps = Number(values.reps ?? 0);
        const arms = base === "both" ? 4 : 2;
        const runs = arms * reps;
        const notes = base === "both" ? [m.noteReuse] : [];
        return estimateCells([{ caseId, modelId, runs }], arms, notes);
      }}
      initial={initial}
      onCreate={async (values) => {
        const source = String(values.source);
        const skill = String(values.skill);
        const caseId = String(values.case);
        const modelId = String(values.model);
        const base = String(values.base) as "minimal" | "full" | "both";
        const reps = Number(values.reps);
        const reuse = values.reuse !== "0";
        if (source === "case") {
          const candidate = candidateFor(skill);
          if (candidate && !candidate.staged) {
            await api.stageCaseCandidateSkill(candidate.caseId, skill);
          }
        }
        const arm = armFor(modelId);
        const result = await api.createSkillTrialOnBase({
          skill,
          model: arm.model,
          harness: arm.harness,
          case_ids: [caseId],
          source: source === "catalog" ? "catalog" : "staged",
          base,
          repetitions: reps,
          reuse_measured: reuse
        });
        return {
          ids: result.experiments.map((experiment) => experiment.experiment_id),
          reused: result.reused.map((experiment) => experiment.experiment_id),
          warnings: result.experiments.flatMap((experiment) => experiment.warnings ?? [])
        };
      }}
      reviewExtra={(values, set) =>
        values.base === "both" ? (
          <label className="checkbox">
            <input
              checked={values.reuse !== "0"}
              onChange={(event) => set("reuse", event.target.checked ? "1" : "0")}
              type="checkbox"
            />
            <span>
              {m.reuseLabel} <span className="muted small">{m.reuseHint}</span>
            </span>
          </label>
        ) : null
      }
      cli={(values) =>
        String(values.source) === "catalog"
          ? `make add-skill-trial SKILL="${String(values.skill)}" MODEL="${String(values.model)}" CASES="${String(values.case)}"`
          : `make stage-candidate SKILL="${String(values.skill)}"`
      }
      outcome={m.outcome}
      steps={steps}
      title={m.title}
    />
  );
}
