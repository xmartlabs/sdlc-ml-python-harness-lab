import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../../api";
import { FlowDiagram } from "../../components/HubPage";
import { GuideFlow } from "../../components/guide/GuideFlow";
import type { GuideStep, GuideValues } from "../../components/guide/GuideFlow";
import {
  armFor,
  estimateCells,
  launchBlocker,
  launchExperiments,
  loadCases,
  loadClis,
  loadModels,
  loadNative
} from "../../components/guide/guideData";
import { armLabel, HarnessModelChooser } from "../../components/guide/HarnessModelChooser";
import { defineMessages, useMessages } from "../../i18n";
import type {
  CaseRecord,
  ExperimentRecord,
  ModelOption,
  NativeModelRecord,
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
    gotTitle: "What the audit found",
    got: (misses: number, runs: number) =>
      misses === 0
        ? `The auditor read ${runs} run${runs === 1 ? "" : "s"} and found no check the skill misses. You can still edit the draft below.`
        : `The auditor read ${runs} run${runs === 1 ? "" : "s"} and found ${misses} check${misses === 1 ? "" : "s"} the skill does not make the agent pass. It wrote a v2 that addresses them.`,
    nextTitle: "What happens next",
    nextSteps: [
      "Read what it misses below, and edit the v2 draft if you disagree.",
      "Stage it: the v2 is saved as a new skill. The original is not touched and nothing runs yet.",
      "Then v1 vs v2: same case, same model, only the version changes. The verdict says whether v2 fixed it."
    ],
    stagedTitle: "v2 is ready to measure",
    stagedBody: (v1: string, v2: string) =>
      `${v2} is staged next to ${v1}. Nothing ran yet, and ${v1} is unchanged. The next step runs both on the same case and model, so the only difference is the version.`,
    abTitle: "Measure v1 vs v2",
    abBody: (v1: string, v2: string) =>
      `Pick where to measure: the case the audit read is chosen. ${v1} and ${v2} run with the same model and base; read the case checks per version.`,
    afterTitle: "After the runs",
    afterSteps: [
      "v2 passes more checks (the ones the audit named): promote it in Skills → Staged → Promote, and it replaces v1.",
      "No difference, or v2 is worse: audit again from these new runs, or keep v1."
    ],
    nodes: { case: "Case", model: "Model", base: "Base", reps: "Repetitions" },
    caseQ: "Which case?",
    modelQ: "Which agent and model?",
    armHint: "The tab is the agent CLI that runs the model; under it, only the models it can drive.",
    baseQ: "On which base?",
    baseMinimal: "Minimal",
    baseFull: "Full",
    repsQ: "How many times each?",
    outcome:
      "v1 vs v2 on the same case and model. If v2 wins, promote it (Skills → Staged → Promote); if not, audit again.",
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
    gotTitle: "Qué encontró la auditoría",
    got: (misses: number, runs: number) =>
      misses === 0
        ? `El auditor leyó ${runs} corrida${runs === 1 ? "" : "s"} y no encontró chequeos que la skill no cubra. Igual podés editar el borrador de abajo.`
        : `El auditor leyó ${runs} corrida${runs === 1 ? "" : "s"} y encontró ${misses} chequeo${misses === 1 ? "" : "s"} que la skill no hace pasar al agente. Escribió una v2 que los ataca.`,
    nextTitle: "Qué sigue",
    nextSteps: [
      "Leé abajo qué le falta, y editá el borrador v2 si no estás de acuerdo.",
      "Preparala: la v2 se guarda como una skill nueva. La original no se toca y todavía no corre nada.",
      "Después v1 contra v2: mismo caso, mismo modelo, solo cambia la versión. El veredicto dice si la v2 lo arregló."
    ],
    stagedTitle: "La v2 está lista para medir",
    stagedBody: (v1: string, v2: string) =>
      `${v2} quedó preparada al lado de ${v1}. Todavía no corrió nada y ${v1} no cambió. El paso siguiente corre las dos en el mismo caso y modelo, así la única diferencia es la versión.`,
    abTitle: "Medir v1 contra v2",
    abBody: (v1: string, v2: string) =>
      `Elegí dónde medir: ya está elegido el caso que leyó la auditoría. ${v1} y ${v2} corren con el mismo modelo y base; mirá los chequeos del caso por versión.`,
    afterTitle: "Después de las corridas",
    afterSteps: [
      "La v2 pasa más chequeos (los que nombró la auditoría): promovela en Skills → Preparadas → Promover, y reemplaza a la v1.",
      "Sin diferencia, o la v2 es peor: auditá de nuevo con estas corridas nuevas, o quedate con la v1."
    ],
    nodes: { case: "Caso", model: "Modelo", base: "Base", reps: "Repeticiones" },
    caseQ: "¿En qué caso?",
    modelQ: "¿Qué agente y modelo?",
    armHint: "La pestaña es la CLI de agente que corre el modelo; abajo, solo los modelos que puede manejar.",
    baseQ: "¿Sobre qué base?",
    baseMinimal: "Minimal",
    baseFull: "Full",
    repsQ: "¿Cuántas veces cada uno?",
    outcome:
      "v1 contra v2, en el mismo caso y modelo. Si gana la v2, promovela (Skills → Preparadas → Promover); si no, auditá de nuevo.",
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
  const [native, setNative] = useState<Record<string, NativeModelRecord[]>>({});
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
    loadNative().then(setNative);
    void loadClis();
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
        // The "ready to measure" box is at the top; the button that got here
        // is at the bottom. Bring the next step into view.
        window.requestAnimationFrame(() =>
          document.querySelector(".improve-next")?.scrollIntoView({ behavior: "smooth", block: "start" })
        );
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
        hint: m.armHint,
        kind: "custom",
        display: (value: string) => armLabel(value, models, native),
        render: (values: GuideValues, pick: (value: string) => void) => (
          <HarnessModelChooser onPick={pick} value={String(values.model ?? "")} />
        )
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
      <div className="improve-flow">
        <FlowDiagram current={4} steps={loopSteps} />
        <div className="callout improve-next">
          <h3>{m.abTitle}</h3>
          <p>{m.abBody(skill, v2For)}</p>
          <h3>{m.afterTitle}</h3>
          <ol>
            {m.afterSteps.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ol>
        </div>
        <GuideFlow
          estimate={(values) => {
            const caseId = String(values.case);
            const modelId = String(values.model);
            const reps = Number(values.reps ?? 0);
            return Promise.all([
              estimateCells([{ caseId, modelId, runs: 2 * reps }], 2),
              launchBlocker(cases.find((row) => row.case_id === caseId), [modelId])
            ]).then(([cells, blocker]) => ({ ...cells, blocker }));
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
              effort: arm.effort,
              case_ids: [caseId],
              base: values.base as "minimal" | "full",
              repetitions: Number(values.reps)
            });
            return { ids: [created.experiment_id], warnings: created.warnings };
          }}
          onLaunch={(values, created) =>
            launchExperiments(created.ids, cases.find((row) => row.case_id === String(values.case)), String(values.model))
          }
          outcome={m.outcome}
          cli={(values) =>
            `make refine-skill SKILL=${skill} CANDIDATE=${v2For} MODEL=${armFor(String(values.model)).model} HARNESS=${armFor(String(values.model)).harness} CASES="${String(values.case)}"`
          }
          steps={steps}
          title={m.title}
        />
      </div>
    );
  }

  return (
    // Inside the Skills page now (ADR-0093 amendment): the page owns the title.
    <div className="improve-flow">
      <FlowDiagram
        current={phase === "a" ? (busy ? 1 : 0) : v2For ? 3 : 2}
        steps={loopSteps}
      />

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
              {v2For ? (
                <div className="callout callout--ok improve-next">
                  <h3>{m.stagedTitle}</h3>
                  <p>{m.stagedBody(skill, v2For)}</p>
                  <div className="guide-actions">
                    <button className="primary" onClick={() => setPhase("c")} type="button">
                      {m.continueToAb} →
                    </button>
                  </div>
                </div>
              ) : (
                <div className="callout improve-next">
                  <h3>{m.gotTitle}</h3>
                  <p>{m.got(audit.misses.length, audit.runs_read)}</p>
                  <h3>{m.nextTitle}</h3>
                  <ol>
                    {m.nextSteps.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ol>
                </div>
              )}
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
                <button className="primary" disabled={busy || !draftName.trim() || Boolean(v2For)} onClick={stage} type="button">
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
    </div>
  );
}
