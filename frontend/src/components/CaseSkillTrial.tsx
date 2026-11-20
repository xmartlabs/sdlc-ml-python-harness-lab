import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { CaseRecord, ExperimentRecord, StagedSkillRecord } from "../types";
import { ErrorBanner } from "./ErrorBanner";

/**
 * "Test a skill on this case" (ADR-0091): the three paths a case that ships a
 * skill offers, in one panel.
 *
 * 1. Try the case's skill — stage its candidate and trial it.
 * 2. Improve it — edit the staged copy (Skills → Import), trial again.
 * 3. Bring yours — any staged skill, trialled the same way.
 *
 * Every path creates the same two-arm experiment: no skill against the staged
 * skill, asked for (Enforce), one model and harness pinned in both arms. It is
 * never launched here: the link opens it in Experiments, behind the shared
 * launch form and preflight.
 */

type Candidate = { name: string; source: string; staged: boolean; status: string | null };

const DEFAULT_MODEL = "claude-haiku-4-5-20251001";

/** Repetitions per arm from the typed text: an integer 1–10, or null. */
export function parseReps(text: string): number | null {
  if (!/^\d{1,2}$/.test(text.trim())) return null;
  const value = Number(text);
  return value >= 1 && value <= 10 ? value : null;
}

const MESSAGES = defineMessages({
  en: {
    heading: "Test a skill on this case",
    lede: "This case ships a skill made for it. Trial it as it is, improve it, or bring your own — each creates the same A/B: the same harness without the skill against with it, asked for.",
    ledeNoCandidate:
      "Bring a skill and trial it here: the same harness without it against with it, asked for.",
    paths: {
      try: "Try the case's skill",
      improve: "Improve it",
      bring: "Bring yours"
    },
    caseSkill: "Ships with this case",
    staged: "staged",
    notStaged: "not staged yet",
    stage: "Stage it",
    restage: "Re-read from the case",
    restageHint: "drops your edits to the staged copy",
    improveHint:
      "Edit the staged copy in Skills → Import, then trial it again. Each edit is a new arm (its content hash changes), so trials of v1 and v2 stay apart.",
    openEditor: "Edit the staged copy",
    bringHint: "Import from GitHub, a SKILL.md or a .zip — then pick it below.",
    openImport: "Import a skill",
    skill: "Skill to trial",
    pick: "Pick a staged skill",
    model: "Model",
    reps: "Repetitions per arm",
    repsHint: "1–10. Two arms, so the runs are twice this. Read a delta only from 5+ per arm.",
    create: "Create the A/B",
    createRuns: (runs: number) => `Create the A/B (${runs} runs)`,
    creating: "Creating…",
    created: (id: string, model: string) =>
      `Created ${id}: both arms run ${model} on the claude harness, one with the skill (asked for) and one without.`,
    open: "Open it to launch",
    openHint: "Launching happens there, with the same preflight as every experiment.",
    readFirst:
      "Read skill_invoked first (near 1, or the arm measured nothing), then this case's own checks.",
    cli: "CLI equivalent"
  },
  es: {
    heading: "Probá una skill en este caso",
    lede: "Este caso trae una skill hecha para él. Probala como está, mejorala o traé la tuya: cada camino crea el mismo A/B, el mismo arnés sin la skill contra con ella, pidiendo que la use.",
    ledeNoCandidate:
      "Traé una skill y probala acá: el mismo arnés sin ella contra con ella, pidiendo que la use.",
    paths: {
      try: "Probá la skill del caso",
      improve: "Mejorala",
      bring: "Traé la tuya"
    },
    caseSkill: "Viene con este caso",
    staged: "preparada",
    notStaged: "todavía sin preparar",
    stage: "Prepararla",
    restage: "Volver a leerla del caso",
    restageHint: "descarta tus cambios a la copia preparada",
    improveHint:
      "Editá la copia preparada en Skills → Importar y volvé a probarla. Cada cambio es un brazo nuevo (cambia su hash de contenido), así que las pruebas de v1 y v2 no se mezclan.",
    openEditor: "Editar la copia preparada",
    bringHint: "Importala desde GitHub, un SKILL.md o un .zip, y elegila abajo.",
    openImport: "Importar una skill",
    skill: "Skill a probar",
    pick: "Elegí una skill preparada",
    model: "Modelo",
    reps: "Repeticiones por brazo",
    repsHint: "De 1 a 10. Son dos brazos, así que las corridas son el doble. Leé una diferencia recién con 5 o más por brazo.",
    create: "Crear el A/B",
    createRuns: (runs: number) => `Crear el A/B (${runs} corridas)`,
    creating: "Creando…",
    created: (id: string, model: string) =>
      `Se creó ${id}: los dos brazos corren ${model} con el arnés claude, uno con la skill (pidiendo que la use) y otro sin ella.`,
    open: "Abrilo para lanzarlo",
    openHint: "Se lanza ahí, con el mismo preflight que cualquier experimento.",
    readFirst:
      "Leé primero skill_invoked (cerca de 1, o el brazo no midió nada) y después los chequeos propios de este caso.",
    cli: "Equivalente en CLI"
  }
});

export function CaseSkillTrial({ caseRecord }: { caseRecord: CaseRecord }) {
  const m = useMessages(MESSAGES);
  const caseId = caseRecord.case_id;
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [staged, setStaged] = useState<StagedSkillRecord[]>([]);
  const [skill, setSkill] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL);
  // Kept as typed text: clamping on every keystroke turned "2" into "12" → 10
  // (2026-11-19, 20 runs instead of 4). It is read once, on submit.
  const [repsText, setRepsText] = useState("3");
  const reps = parseReps(repsText);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<(ExperimentRecord & { warnings?: string[] }) | null>(null);

  const refresh = () => {
    Promise.all([api.listCaseCandidateSkills(caseId), api.listStagedSkills()])
      .then(([rows, stagedRows]) => {
        setCandidates(rows);
        const active = stagedRows.filter((row) => row.status !== "archived");
        setStaged(active);
        // Preselect the case's own skill once it is staged.
        setSkill((current) => current || rows.find((row) => row.staged)?.name || "");
      })
      .catch(() => undefined);
  };

  useEffect(() => {
    setCandidates([]);
    setSkill("");
    setCreated(null);
    setError(null);
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  const stage = (name: string, replace: boolean) => {
    setBusy(true);
    setError(null);
    api
      .stageCaseCandidateSkill(caseId, name, replace)
      .then(() => {
        setSkill(name);
        refresh();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const createTrial = () => {
    setBusy(true);
    setError(null);
    setCreated(null);
    api
      .createSkillTrial({
        skill,
        model: model.trim(),
        case_ids: [caseId],
        repetitions: reps ?? 1,
        harness: "claude",
        source: "staged"
      })
      .then(setCreated)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const hasCandidate = candidates.length > 0;

  return (
    <section className="panel case-skill-trial">
      <div className="panel-head">
        <h2>{m.heading}</h2>
      </div>
      <p className="muted">{hasCandidate ? m.lede : m.ledeNoCandidate}</p>

      <ol className="case-skill-paths">
        {hasCandidate ? (
          <li>
            <strong>1 · {m.paths.try}</strong>
            {candidates.map((candidate) => (
              <div className="case-skill-candidate" key={candidate.name}>
                <code>{candidate.name}</code>
                <span className={candidate.staged ? "badge ok" : "badge neutral"}>
                  {candidate.staged ? m.staged : m.notStaged}
                </span>
                <small className="muted">
                  {m.caseSkill}: {candidate.source}
                </small>
                {candidate.staged ? (
                  <button
                    className="button-link"
                    disabled={busy}
                    onClick={() => stage(candidate.name, true)}
                    title={m.restageHint}
                    type="button"
                  >
                    {m.restage}
                  </button>
                ) : (
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() => stage(candidate.name, false)}
                    type="button"
                  >
                    {m.stage}
                  </button>
                )}
              </div>
            ))}
          </li>
        ) : null}
        {hasCandidate ? (
          <li>
            <strong>2 · {m.paths.improve}</strong>
            <span className="muted">{m.improveHint}</span>
            <Link className="button-link" to="/evidence/skills?tab=import">
              {m.openEditor}
            </Link>
          </li>
        ) : null}
        <li>
          <strong>
            {hasCandidate ? "3 · " : ""}
            {m.paths.bring}
          </strong>
          <span className="muted">{m.bringHint}</span>
          <Link className="button-link" to="/evidence/skills?tab=import">
            {m.openImport}
          </Link>
        </li>
      </ol>

      <div className="form-grid">
        <label>
          <span>{m.skill}</span>
          <select onChange={(event) => setSkill(event.target.value)} value={skill}>
            <option value="">{m.pick}</option>
            {staged.map((row) => (
              <option key={row.name} value={row.name}>
                {row.name}
                {candidates.some((candidate) => candidate.name === row.name)
                  ? ` — ${m.caseSkill.toLowerCase()}`
                  : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{m.model}</span>
          <input onChange={(event) => setModel(event.target.value)} value={model} />
        </label>
        <label>
          <span>{m.reps}</span>
          <input
            max={10}
            min={1}
            inputMode="numeric"
            onChange={(event) => setRepsText(event.target.value)}
            type="number"
            value={repsText}
          />
          <small className="muted">{m.repsHint}</small>
        </label>
      </div>

      <div className="case-skill-actions">
        <button
          className="primary"
          disabled={busy || !skill || !model.trim() || reps === null}
          onClick={createTrial}
          type="button"
        >
          {busy ? m.creating : reps === null ? m.create : m.createRuns(2 * reps)}
        </button>
        <span className="muted">{m.readFirst}</span>
      </div>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {created ? (
        <div className="callout callout--ok">
          <p>{m.created(created.experiment_id, model.trim())}</p>
          {created.warnings?.map((warning) => (
            <p className="muted" key={warning}>
              {warning}
            </p>
          ))}
          <p>
            <Link to={`/evaluate/experiments?experiment=${created.experiment_id}`}>{m.open}</Link>{" "}
            <span className="muted">{m.openHint}</span>
          </p>
        </div>
      ) : null}

      <details className="case-skill-cli">
        <summary className="muted">{m.cli}</summary>
        <pre>
          <code>
            {hasCandidate ? `make stage-candidate SKILL=${candidates[0].name}\n` : ""}
            {"# New evaluation → Add skills → tick it → Enforce skill use"}
          </code>
        </pre>
      </details>
    </section>
  );
}
