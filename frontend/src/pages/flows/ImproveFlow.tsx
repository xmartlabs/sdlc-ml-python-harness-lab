import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api";
import { FlowDiagram } from "../../components/HubPage";
import { GuideFlow } from "../../components/guide/GuideFlow";
import type { GuideStep, GuideValues } from "../../components/guide/GuideFlow";
import { armFor, estimateCells, loadCases, loadModels } from "../../components/guide/guideData";
import { defineMessages, useMessages } from "../../i18n";
import type {
  CaseRecord,
  ExperimentRecord,
  ModelOption,
  SkillAuditResult,
  StagedSkillRecord
} from "../../types";
import { caseOptions } from "./ModelsFlow";

/**
 * Improve a skill with the LLM as auditor (ADR-0092, ADR-0093). Unlike the
 * other guided flows, this one is not a single `GuideFlow` from the start: an
 * LLM call sits in the middle, and nothing is staged until the person reviews
 * and edits the draft. Three phases:
 *
 * A. pick the staged skill and the experiment whose runs carried it, then run
 *    the audit (`api.auditSkill`, writes nothing);
 * B. review the diagnosis and the v2 draft, edit it, then stage it
 *    (`api.stageSkillDraft`, the only step that writes);
 * C. a `GuideFlow` for v1 vs v2, created but never launched
 *    (`api.createSkillRefine`).
 */

const MESSAGES = defineMessages({
  en: {
    title: "Improve a skill",
    loop: {
      runs: "Runs with the skill",
      audit: "LLM audit",
      draft: "v2 draft",
      staged: "Staged",
      ab: "v1 vs v2",
      promote: "Promote or audit again"
    },
    phaseA: "1 · Pick and audit",
    phaseB: "2 · Review the audit",
    phaseC: "3 · v1 vs v2",
    skillQ: "Which staged skill?",
    skillHint:
      "Governed catalog skills can be refined too, but need a staged v2 written by hand — this flow stages one for you, so it starts from a staged skill.",
    noSkills: "No staged skill yet. Stage one from Skills → Import, or finish an earlier audit here.",
    pickSkill: "Pick a staged skill",
    experimentQ: "Which experiment carried it?",
    experimentHint: "Only experiments whose arms ran this skill are listed.",
    noExperiments: "No experiment found that carried this skill. Run a skill trial first.",
    pickExperiment: "Pick an experiment",
    runAudit: "Run the audit",
    auditing: "The auditor is reading the runs… this can take 30 to 60 seconds.",
    back: "Back",
    summary: "Summary",
    covers: "What it already covers",
    misses: "What it misses",
    check: "Check",
    why: "Why",
    fix: "Fix",
    provenance: (runs: number, model: string) => `Read ${runs} run${runs === 1 ? "" : "s"} · auditor: ${model}`,
    draftLabel: "v2 draft, editable before staging",
    stageAs: (name: string) => `Stage as ${name}`,
    staging: "Staging…",
    nameTaken: "exists already",
    nameTakenHint: "already exists. Pick another name for the staged copy.",
    newName: "New name",
    stagedOk: (name: string) => `Staged as ${name}.`,
    continueToAb: "Continue to v1 vs v2",
    nodes: { case: "Case", model: "Model", base: "Base", reps: "Repetitions" },
    caseQ: "Which case?",
    modelQ: "Which model?",
    baseQ: "On which base?",
    baseMinimal: "Minimal",
    baseFull: "Full",
    repsQ: "How many times each?",
    local: "local · this machine",
    cloud: "cloud",
    outcome:
      "v1 vs v2 on the same case and model. If v2 wins, promote it (Skills → Promote); if not, audit again.",
    auditorModel: "Auditor model (optional)",
    auditorModelHint: "Leave empty to use the lab's default auditor."
  },
  es: {
    title: "Mejorar una skill",
    loop: {
      runs: "Corridas con la skill",
      audit: "Auditoría del LLM",
      draft: "Borrador v2",
      staged: "Preparada",
      ab: "v1 vs v2",
      promote: "Promover o auditar de nuevo"
    },
    phaseA: "1 · Elegir y auditar",
    phaseB: "2 · Revisar la auditoría",
    phaseC: "3 · v1 vs v2",
    skillQ: "¿Qué skill preparada?",
    skillHint:
      "Las skills del catálogo gobernado también se pueden mejorar, pero necesitan un v2 preparado a mano: este flujo prepara uno por vos, así que arranca de una skill ya preparada.",
    noSkills: "Todavía no hay ninguna skill preparada. Preparala en Skills → Importar, o terminá una auditoría anterior acá.",
    pickSkill: "Elegí una skill preparada",
    experimentQ: "¿Qué experimento la usó?",
    experimentHint: "Se listan solo los experimentos cuyos brazos corrieron esta skill.",
    noExperiments: "No se encontró ningún experimento que haya usado esta skill. Primero corré una prueba de skill.",
    pickExperiment: "Elegí un experimento",
    runAudit: "Correr la auditoría",
    auditing: "El auditor está leyendo las corridas… puede tardar de 30 a 60 segundos.",
    back: "Volver",
    summary: "Resumen",
    covers: "Lo que ya cubre",
    misses: "Lo que le falta",
    check: "Chequeo",
    why: "Por qué",
    fix: "Arreglo",
    provenance: (runs: number, model: string) => `Leyó ${runs} corrida${runs === 1 ? "" : "s"} · auditor: ${model}`,
    draftLabel: "Borrador v2, editable antes de prepararlo",
    stageAs: (name: string) => `Preparar como ${name}`,
    staging: "Preparando…",
    nameTaken: "ya existe",
    nameTakenHint: "ya existe. Elegí otro nombre para la copia preparada.",
    newName: "Nombre nuevo",
    stagedOk: (name: string) => `Preparada como ${name}.`,
    continueToAb: "Seguir a v1 vs v2",
    nodes: { case: "Caso", model: "Modelo", base: "Base", reps: "Repeticiones" },
    caseQ: "¿En qué caso?",
    modelQ: "¿Qué modelo?",
    baseQ: "¿Sobre qué base?",
    baseMinimal: "Mínima",
    baseFull: "Completa",
    repsQ: "¿Cuántas veces cada uno?",
    local: "local · esta máquina",
    cloud: "nube",
    outcome:
      "v1 contra v2, en el mismo caso y modelo. Si gana v2, promovelo (Skills → Promover); si no, auditá de nuevo.",
    auditorModel: "Modelo auditor (opcional)",
    auditorModelHint: "Dejalo vacío para usar el auditor por defecto del lab."
  }
});

/** Experiments whose arms likely carried this skill (a heuristic; the backend validates). */
function experimentsForSkill(skill: string, experiments: ExperimentRecord[]): ExperimentRecord[] {
  const needle = skill.toLowerCase();
  return experiments.filter((row) => {
    const id = row.experiment_id.toLowerCase();
    const name = (row.name ?? "").toLowerCase();
    if (id.includes(needle) || name.includes(needle)) return true;
    const familyId = (row.family_id ?? "").toLowerCase();
    if (familyId === "candidate-skill-trials") return true;
    return familyId.startsWith("skill-") || familyId.startsWith("refine-");
  });
}

export function ImproveFlow() {
  const m = useMessages(MESSAGES);
  const [params] = useSearchParams();
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [staged, setStaged] = useState<StagedSkillRecord[]>([]);
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([]);

  const [phase, setPhase] = useState<"a" | "b" | "c">("a");
  const [skill, setSkill] = useState(params.get("skill") ?? "");
  const [experimentId, setExperimentId] = useState(params.get("experiment") ?? "");
  const [auditorModel, setAuditorModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [audit, setAudit] = useState<SkillAuditResult | null>(null);
  const [draftText, setDraftText] = useState("");
  const [draftName, setDraftName] = useState("");
  // The staged v2 and the skill it is a v2 *of*. Phase C only ever compares a
  // skill with a v2 staged from its own audit (review finding 2026-11-19).
  const [stagedOk, setStagedOk] = useState<{ skill: string; name: string } | null>(null);
  const v2For = stagedOk && stagedOk.skill === skill ? stagedOk.name : null;

  useEffect(() => {
    loadCases().then(setCases);
    loadModels().then(setModels);
    api
      .listStagedSkills()
      .then((rows) => setStaged(rows.filter((row) => row.status !== "archived")))
      .catch(() => undefined);
    api
      .listExperiments()
      .then(setExperiments)
      .catch(() => undefined);
  }, []);

  const loopSteps = [
    { title: m.loop.runs, detail: "" },
    { title: m.loop.audit, detail: "" },
    { title: m.loop.draft, detail: "" },
    { title: m.loop.staged, detail: "" },
    { title: m.loop.ab, detail: "" },
    { title: m.loop.promote, detail: "" }
  ];

  const candidateExperiments = skill ? experimentsForSkill(skill, experiments) : [];

  const runAudit = () => {
    setBusy(true);
    setError(null);
    api
      .auditSkill(skill, experimentId, auditorModel.trim())
      .then((result) => {
        setStagedOk(null);
        setAudit(result);
        setDraftText(result.v2);
        setDraftName(result.v2_name);
        setPhase("b");
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const stage = () => {
    setBusy(true);
    setError(null);
    api
      .stageSkillDraft(draftName, draftText)
      .then(() => {
        setStagedOk({ skill, name: draftName });
        api
          .listStagedSkills()
          .then((rows) => setStaged(rows.filter((row) => row.status !== "archived")))
          .catch(() => undefined);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  if (phase === "c" && audit && v2For) {
    const defaultCase = experiments.find((row) => row.experiment_id === experimentId)?.case_ids[0] ?? "";
    const steps: GuideStep[] = [
      { key: "case", node: m.nodes.case, question: m.caseQ, kind: "one", options: () => caseOptions(cases) },
      {
        key: "model",
        node: m.nodes.model,
        question: m.modelQ,
        kind: "one",
        options: () =>
          models.map((model) => ({
            id: model.id,
            label: model.label || model.id,
            sub: /lmstudio|ollama/i.test(model.id) ? m.local : m.cloud
          }))
      },
      {
        key: "base",
        node: m.nodes.base,
        question: m.baseQ,
        kind: "one",
        options: () => [
          { id: "minimal", label: m.baseMinimal },
          { id: "full", label: m.baseFull }
        ]
      },
      { key: "reps", node: m.nodes.reps, question: m.repsQ, kind: "reps" }
    ];
    const initial: GuideValues = { base: "full", ...(defaultCase ? { case: defaultCase } : {}) };

    return (
      <section className="page">
        <header className="page-header">
          <div>
            <p className="eyebrow">{m.title}</p>
            <h1>{m.title}</h1>
          </div>
        </header>
        <FlowDiagram steps={loopSteps} />
        <p className="muted">{m.phaseC}</p>
        <GuideFlow
          estimate={(values) => {
            const caseId = String(values.case);
            const modelId = String(values.model);
            const reps = Number(values.reps ?? 0);
            return estimateCells([{ caseId, modelId, runs: 2 * reps }], 2);
          }}
          initial={initial}
          onCreate={async (values) => {
            const caseId = String(values.case);
            const modelId = String(values.model);
            const arm = armFor(modelId);
            const created = await api.createSkillRefine({
              skill,
              candidate: v2For,
              model: arm.model,
              harness: arm.harness,
              case_ids: [caseId],
              base: values.base as "minimal" | "full",
              repetitions: Number(values.reps)
            });
            return { ids: [created.experiment_id], warnings: created.warnings };
          }}
          outcome={m.outcome}
          cli={(values) =>
            `make refine-skill SKILL=${skill} CANDIDATE=${v2For} MODEL=${String(values.model)} CASES="${String(values.case)}"`
          }
          steps={steps}
          title={m.title}
        />
      </section>
    );
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{m.title}</p>
          <h1>{m.title}</h1>
        </div>
      </header>
      <FlowDiagram steps={loopSteps} />

      {phase === "a" ? (
        <div className="guide-body">
          <div className="guide-main">
            <section className="guide-step">
              <span className="guide-step-label">{m.phaseA}</span>
              <h2>{m.skillQ}</h2>
              <p className="muted">{m.skillHint}</p>
              {staged.length === 0 ? (
                <p className="muted">{m.noSkills}</p>
              ) : (
                <div className="guide-options">
                  {staged.map((row) => (
                    <button
                      aria-pressed={skill === row.name}
                      className="guide-option"
                      key={row.name}
                      onClick={() => {
                        setSkill(row.name);
                        setAudit(null);
                        setStagedOk(null);
                        setExperimentId("");
                        setAudit(null);
                      }}
                      type="button"
                    >
                      <strong>{row.name}</strong>
                      {row.description ? <span className="muted">{row.description.slice(0, 120)}</span> : null}
                    </button>
                  ))}
                </div>
              )}
            </section>

            {skill ? (
              <section className="guide-step">
                <h2>{m.experimentQ}</h2>
                <p className="muted">{m.experimentHint}</p>
                {candidateExperiments.length === 0 ? (
                  <p className="muted">{m.noExperiments}</p>
                ) : (
                  <div className="guide-options">
                    {candidateExperiments.map((row) => (
                      <button
                        aria-pressed={experimentId === row.experiment_id}
                        className="guide-option"
                        key={row.experiment_id}
                        onClick={() => setExperimentId(row.experiment_id)}
                        type="button"
                      >
                        <strong>{row.experiment_id}</strong>
                        {row.name ? <span className="muted">{row.name}</span> : null}
                      </button>
                    ))}
                  </div>
                )}
              </section>
            ) : null}

            {skill && experimentId ? (
              <section className="guide-step">
                <label>
                  <span className="guide-field-label">{m.auditorModel}</span>
                  <input onChange={(event) => setAuditorModel(event.target.value)} value={auditorModel} />
                </label>
                <p className="muted guide-small">{m.auditorModelHint}</p>
                <div className="guide-actions">
                  <button className="primary" disabled={busy} onClick={runAudit} type="button">
                    {busy ? m.auditing : m.runAudit}
                  </button>
                </div>
              </section>
            ) : null}

            {error ? <p className="guide-error">{error}</p> : null}
          </div>
        </div>
      ) : null}

      {phase === "b" && audit ? (
        <div className="guide-body">
          <div className="guide-main">
            <section aria-live="polite" className="guide-step">
              <span className="guide-step-label">{m.phaseB}</span>
              <h2>{m.summary}</h2>
              <p>{audit.summary}</p>
              <p className="muted guide-small">{m.provenance(audit.runs_read, audit.model)}</p>

              {audit.covers.length > 0 ? (
                <>
                  <h3>{m.covers}</h3>
                  <ul>
                    {audit.covers.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              ) : null}

              {audit.misses.length > 0 ? (
                <>
                  <h3>{m.misses}</h3>
                  <div className="guide-review">
                    {audit.misses.map((miss, i) => (
                      <div key={`${miss.check}-${i}`}>
                        <dt>{m.check}</dt>
                        <dd>{miss.check}</dd>
                        <dt>{m.why}</dt>
                        <dd>{miss.why}</dd>
                        <dt>{m.fix}</dt>
                        <dd>{miss.fix}</dd>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}

              <label>
                <span className="guide-field-label">{m.draftLabel}</span>
                <textarea
                  onChange={(event) => setDraftText(event.target.value)}
                  rows={16}
                  style={{ width: "100%", fontFamily: "monospace", fontSize: "0.85rem" }}
                  value={draftText}
                />
              </label>

              <label>
                <span className="guide-field-label">{m.newName}</span>
                <input onChange={(event) => setDraftName(event.target.value)} value={draftName} />
              </label>

              {error ? <p className="guide-error">{error}</p> : null}

              <div className="guide-actions">
                <button className="secondary" disabled={busy} onClick={() => {
                    setPhase("a");
                    setAudit(null);
                    setStagedOk(null);
                  }}
                  type="button"
                >
                  {m.back}
                </button>
                <button className="primary" disabled={busy || !draftName.trim()} onClick={stage} type="button">
                  {busy ? m.staging : m.stageAs(draftName)}
                </button>
              </div>

              {v2For ? (
                <div className="callout callout--ok">
                  <p>{m.stagedOk(v2For)}</p>
                  <button className="primary" onClick={() => setPhase("c")} type="button">
                    {m.continueToAb}
                  </button>
                </div>
              ) : null}
            </section>
          </div>
        </div>
      ) : null}
    </section>
  );
}
