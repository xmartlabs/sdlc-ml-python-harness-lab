import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { AdhocRunReview } from "../components/AdhocRunReview";
import { ErrorBanner } from "../components/ErrorBanner";
import { StateBadge } from "../components/StateBadge";
import { Spinner } from "../components/Spinner";
import { armFor } from "../components/guide/guideData";
import { HarnessModelChooser } from "../components/guide/HarnessModelChooser";
import { formatRunDate } from "../dates";
import { harnessLabel } from "../harnesses";
import { defineMessages, useMessages } from "../i18n";
import { InfoTooltip } from "../components/InfoTooltip";
import { IconStar } from "../components/icons";
import type {
  AdhocHarnessLevel,
  AdhocRunRecord,
  AdhocTaskDetail as AdhocTaskDetailType,
  SkillProfileRecord,
  StagedSkillRecord
} from "../types";

/**
 * One ad hoc task (ADR-0095): what it asks for, the folder it runs against,
 * every run of it side by side, and the review panel for whichever run is
 * selected.
 */

const LIVE_RUN_STATES = new Set(["pending", "running", "scoring", "auditing"]);
const LIVE_APPLY_STATES = new Set(["applying", "undoing"]);
const POLL_MS = 2000;

const MESSAGES = defineMessages({
  en: {
    loading: "Loading task…",
    notFound: "Task not found.",
    prompt: "Prompt",
    folder: "Folder",
    baseCommit: "Base commit",
    headMoved: (n: number) =>
      `Your repo moved ${n} commits since this task was created: applying may conflict.`,
    runWith: "Run with…",
    launch: "Run",
    launching: "Launching…",
    egress: {
      cloud: (provider: string) => `Your code will be sent to ${provider}.`,
      local: "Runs on your local model: nothing leaves this machine."
    },
    level: {
      label: "Agent harness",
      tip: "What the agent sees in the copy besides your code. The lab's harness is added to the copy only: it never reaches your folder, and the change you apply leaves it out.",
      names: { repo: "Your repo", minimal: "Lab minimal", full: "Lab full" } as Record<AdhocHarnessLevel, string>,
      hints: {
        repo: "Only what your repository ships (its CLAUDE.md, skills and agents, if any). Nothing from the lab.",
        minimal: (n: number) =>
          `The lab's harness with the minimal profile: ${n} skills (plan, debug, verify, research, review). Replaces your CLAUDE.md in the copy if you have one.`,
        full: (n: number) =>
          `The lab's whole harness: ${n} skills plus its subagents. Replaces your CLAUDE.md in the copy if you have one.`
      },
      trial: "Skill to try (optional)",
      trialTip: "A staged skill from the Skills page, added on top of the harness above. Useful to see whether one skill changes the result on a real task.",
      noTrial: "None",
      invoke: "Tell the agent to use it",
      invokeTip: "A skill that is only available is often never opened. Ticked, the prompt asks the agent to invoke it."
    },
    runs: {
      title: "Runs",
      colHarness: "Harness",
      colLevel: "Agent harness",
      colModel: "Model",
      colEffort: "Effort",
      colState: "State",
      colTokens: "Tokens",
      colCost: "Cost",
      colMinutes: "Minutes",
      colFiles: "Files",
      colDecision: "Decision",
      colApply: "Apply",
      none: "—",
      unknownCost: "unknown (subscription)",
      localCost: "$0 (local)",
      estimated: (value: string) => `~$${value} estimated`,
      decisions: {
        pending: "pending",
        approved: "approved",
        rejected: "rejected",
        discarded: "discarded"
      },
      applyStates: {
        none: "—",
        applying: "applying",
        applied: "applied",
        undoing: "undoing",
        undone: "undone",
        failed: "failed"
      }
    },
    keep: "Keep",
    kept: "Kept",
    keepTip: "Kept tasks survive \"Delete unkept\" on the task list.",
    deleteTask: "Delete task",
    deleting: "Deleting…",
    deleteConfirm: (name: string) =>
      `Delete "${name}"? Its copy, runs and metrics go too. Your folder is not touched.`,
    errors: {
      load: "Could not load this task.",
      launch: "Could not launch the run."
    }
  },
  es: {
    loading: "Cargando tarea…",
    notFound: "No se encontró la tarea.",
    prompt: "Prompt",
    folder: "Carpeta",
    baseCommit: "Commit base",
    headMoved: (n: number) =>
      `Tu repo avanzó ${n} commits desde que se creó esta tarea: aplicar puede generar conflictos.`,
    runWith: "Correr con…",
    launch: "Correr",
    launching: "Lanzando…",
    egress: {
      cloud: (provider: string) => `Tu código se va a enviar a ${provider}.`,
      local: "Corre en tu modelo local: nada sale de esta máquina."
    },
    level: {
      label: "Harness del agente",
      tip: "Lo que el agente ve en la copia además de tu código. El harness del lab se agrega solo a la copia: nunca llega a tu carpeta y el cambio que aplicás lo deja afuera.",
      names: { repo: "Tu repo", minimal: "Lab mínimo", full: "Lab completo" } as Record<AdhocHarnessLevel, string>,
      hints: {
        repo: "Solo lo que trae tu repositorio (su CLAUDE.md, skills y agentes, si tiene). Nada del lab.",
        minimal: (n: number) =>
          `El harness del lab con el perfil mínimo: ${n} skills (planificar, depurar, verificar, investigar, revisar). Si tenés CLAUDE.md, en la copia lo reemplaza.`,
        full: (n: number) =>
          `El harness completo del lab: ${n} skills más sus subagentes. Si tenés CLAUDE.md, en la copia lo reemplaza.`
      },
      trial: "Skill a probar (opcional)",
      trialTip: "Una skill en borrador de la página Skills, agregada encima del harness elegido. Sirve para ver si una skill cambia el resultado en una tarea real.",
      noTrial: "Ninguna",
      invoke: "Pedirle al agente que la use",
      invokeTip: "Una skill que solo está disponible muchas veces ni se abre. Marcado, el prompt le pide al agente que la invoque."
    },
    runs: {
      title: "Corridas",
      colHarness: "Arnés",
      colLevel: "Harness del agente",
      colModel: "Modelo",
      colEffort: "Esfuerzo",
      colState: "Estado",
      colTokens: "Tokens",
      colCost: "Costo",
      colMinutes: "Minutos",
      colFiles: "Archivos",
      colDecision: "Decisión",
      colApply: "Aplicación",
      none: "—",
      unknownCost: "desconocido (suscripción)",
      localCost: "$0 (local)",
      estimated: (value: string) => `~$${value} estimado`,
      decisions: {
        pending: "pendiente",
        approved: "aprobada",
        rejected: "rechazada",
        discarded: "descartada"
      },
      applyStates: {
        none: "—",
        applying: "aplicando",
        applied: "aplicada",
        undoing: "deshaciendo",
        undone: "deshecha",
        failed: "falló"
      }
    },
    keep: "Guardar",
    kept: "Guardada",
    keepTip: "Las tareas guardadas sobreviven a \"Borrar las no guardadas\" en la lista de tareas.",
    deleteTask: "Borrar tarea",
    deleting: "Borrando…",
    deleteConfirm: (name: string) =>
      `¿Borrar "${name}"? También se van su copia, sus corridas y sus métricas. Tu carpeta no se toca.`,
    errors: {
      load: "No se pudo cargar esta tarea.",
      launch: "No se pudo lanzar la corrida."
    }
  }
});

function isCloudModel(model: string): boolean {
  return !/^(lmstudio|ollama)\//.test(model);
}

function costCell(run: AdhocRunRecord, m: (typeof MESSAGES)["en"]): string {
  // antigravity_cli (ADR-0096): tokens at the Gemini API list price, priced by the lab.
  if (run.cost_usd != null && run.cost_source === "api_list_price_estimate") {
    return m.runs.estimated(run.cost_usd.toFixed(4));
  }
  if (run.cost_usd != null) return `$${run.cost_usd.toFixed(4)}`;
  if (run.estimated_cost_usd != null) return m.runs.estimated(run.estimated_cost_usd.toFixed(2));
  if (run.billing_mode === "subscription") return m.runs.unknownCost;
  if (run.cost_source === "local" || run.cost_source === "self_hosted") return m.runs.localCost;
  return m.runs.none;
}

export function AdhocTaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState(false);
  const m = useMessages(MESSAGES);

  const [detail, setDetail] = useState<AdhocTaskDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [arm, setArm] = useState("");
  const [level, setLevel] = useState<AdhocHarnessLevel>("repo");
  const [trialSkill, setTrialSkill] = useState("");
  const [invokeTrial, setInvokeTrial] = useState(false);
  const [profiles, setProfiles] = useState<Record<string, SkillProfileRecord>>({});
  const [staged, setStaged] = useState<StagedSkillRecord[]>([]);
  const [launching, setLaunching] = useState(false);
  const runPanelRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(() => {
    if (!taskId) return;
    api
      .getAdhocTask(taskId)
      .then((result) => {
        setDetail(result);
        setSelectedRunId((current) => current ?? result.runs[0]?.run_id ?? null);
      })
      .catch(() => setError(m.errors.load));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  useEffect(() => {
    load();
  }, [load]);

  // What each harness level carries, and the staged skills one can try.
  useEffect(() => {
    api
      .listSkillProfiles()
      .then(setProfiles)
      .catch(() => setProfiles({}));
    api
      .listStagedSkills()
      .then((rows) => setStaged(rows.filter((row) => row.status !== "archived")))
      .catch(() => setStaged([]));
  }, []);

  // Poll while anything on this task is still moving.
  useEffect(() => {
    if (!detail) return undefined;
    const live =
      detail.runs.some((run) => LIVE_RUN_STATES.has(run.state) || LIVE_APPLY_STATES.has(run.apply_state)) ?? false;
    if (!live) return undefined;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [detail, load]);

  const launch = async () => {
    if (!taskId || !arm) return;
    const picked = armFor(arm);
    setLaunching(true);
    setError(null);
    try {
      const run = await api.launchAdhocRun(taskId, {
        harness: picked.harness,
        executor_model: picked.model,
        executor_effort: picked.effort || undefined,
        harness_level: level,
        trial_skill: trialSkill || undefined,
        invoke_trial_skill: Boolean(trialSkill) && invokeTrial
      });
      setDetail((current) => (current ? { ...current, runs: [run, ...current.runs] } : current));
      setSelectedRunId(run.run_id);
    } catch (launchError) {
      setError(launchError instanceof Error ? launchError.message : m.errors.launch);
    } finally {
      setLaunching(false);
    }
  };

  const toggleKeep = async () => {
    if (!detail) return;
    const keep = !detail.task.keep;
    setDetail({ ...detail, task: { ...detail.task, keep } });
    try {
      await api.updateAdhocTask(detail.task.task_id, { keep });
    } catch (keepError) {
      setDetail((current) => (current ? { ...current, task: { ...current.task, keep: !keep } } : current));
      setError(keepError instanceof Error ? keepError.message : String(keepError));
    }
  };

  const deleteTask = async () => {
    if (!detail) return;
    if (!window.confirm(m.deleteConfirm(detail.task.title || detail.task.task_id))) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteAdhocTask(detail.task.task_id);
      navigate("/adhoc");
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : String(deleteError));
      setDeleting(false);
    }
  };

  const onRunChanged = (updated: AdhocRunRecord) => {
    setDetail((current) =>
      current
        ? { ...current, runs: current.runs.map((run) => (run.run_id === updated.run_id ? updated : run)) }
        : current
    );
  };

  if (error && !detail) {
    return <ErrorBanner message={error} />;
  }
  if (!detail) {
    return <Spinner />;
  }

  const { task, runs, source } = detail;
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? runs[0] ?? null;
  const pickedArm = arm ? armFor(arm) : null;
  const skillCount = (name: string) => profiles[name]?.skills.length ?? 0;
  const levelHint =
    level === "repo"
      ? m.level.hints.repo
      : level === "minimal"
        ? m.level.hints.minimal(skillCount("minimal"))
        : m.level.hints.full(skillCount("full"));
  const levels: AdhocHarnessLevel[] = ["repo", "minimal", "full"];

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>{task.title || task.task_id}</h1>
          <p className="mono small">
            {m.folder}: {task.host_path} · {task.branch} · {m.baseCommit} {task.base_commit.slice(0, 8)}
          </p>
        </div>
        <div className="page-header-action button-row">
          <button
            aria-pressed={Boolean(task.keep)}
            className={task.keep ? "ghost keep-button active" : "ghost keep-button"}
            onClick={() => void toggleKeep()}
            title={m.keepTip}
            type="button"
          >
            <IconStar filled={Boolean(task.keep)} size={14} /> {task.keep ? m.kept : m.keep}
          </button>
          <button className="danger" disabled={deleting} onClick={() => void deleteTask()} type="button">
            {deleting ? m.deleting : m.deleteTask}
          </button>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {(source.head_moved_by ?? 0) > 0 ? (
        <div className="callout callout--warning">
          <p>{m.headMoved(source.head_moved_by ?? 0)}</p>
        </div>
      ) : null}

      <details className="card">
        <summary>{m.prompt}</summary>
        <p style={{ whiteSpace: "pre-wrap" }}>{task.prompt}</p>
      </details>

      <div className="card" ref={runPanelRef}>
        <h2>{m.runWith}</h2>
        <HarnessModelChooser onPick={setArm} value={arm} />
        <div className="adhoc-level">
          <span className="guide-field-label">
            {m.level.label} <InfoTooltip text={m.level.tip} />
          </span>
          <div className="segmented" role="group">
            {levels.map((name) => (
              <button
                aria-pressed={level === name}
                className={level === name ? "active" : ""}
                key={name}
                onClick={() => setLevel(name)}
                type="button"
              >
                {m.level.names[name]}
              </button>
            ))}
          </div>
          <p className="muted small">{levelHint}</p>
          {staged.length > 0 ? (
            <div className="adhoc-trial">
              <label>
                <span className="guide-field-label">
                  {m.level.trial} <InfoTooltip text={m.level.trialTip} />
                </span>
                <select onChange={(event) => setTrialSkill(event.target.value)} value={trialSkill}>
                  <option value="">{m.level.noTrial}</option>
                  {staged.map((skill) => (
                    <option key={skill.name} value={skill.name}>
                      {skill.name}
                    </option>
                  ))}
                </select>
              </label>
              {trialSkill ? (
                <label className="inline-check">
                  <input checked={invokeTrial} onChange={(event) => setInvokeTrial(event.target.checked)} type="checkbox" />
                  <span>
                    {m.level.invoke} <InfoTooltip text={m.level.invokeTip} />
                  </span>
                </label>
              ) : null}
            </div>
          ) : null}
        </div>
        {pickedArm ? (
          <p className="muted small">
            {isCloudModel(pickedArm.model)
              ? m.egress.cloud(harnessLabel(pickedArm.harness))
              : m.egress.local}
          </p>
        ) : null}
        <div className="button-row">
          <button className="primary" disabled={!arm || launching} onClick={() => void launch()} type="button">
            {launching ? m.launching : m.launch}
          </button>
        </div>
      </div>

      <div className="card">
        <h2>{m.runs.title}</h2>
        {runs.length === 0 ? (
          <p className="muted">{m.runs.none}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.runs.colHarness}</th>
                <th>{m.runs.colLevel}</th>
                <th>{m.runs.colModel}</th>
                <th>{m.runs.colEffort}</th>
                <th>{m.runs.colState}</th>
                <th>{m.runs.colTokens}</th>
                <th>{m.runs.colCost}</th>
                <th>{m.runs.colMinutes}</th>
                <th>{m.runs.colFiles}</th>
                <th>{m.runs.colDecision}</th>
                <th>{m.runs.colApply}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  className={run.run_id === selectedRun?.run_id ? "selectable active" : "selectable"}
                  key={run.run_id}
                  onClick={() => setSelectedRunId(run.run_id)}
                  style={{ cursor: "pointer" }}
                >
                  <td>{harnessLabel(run.harness)}</td>
                  <td className="small">
                    {m.level.names[run.harness_level ?? "repo"]}
                    {run.trial_skills?.length ? ` + ${run.trial_skills.join(", ")}` : ""}
                    {run.skills_invoked?.length ? " *" : ""}
                  </td>
                  <td className="mono small">{run.executor_model}</td>
                  <td>{run.executor_effort || m.runs.none}</td>
                  <td>
                    <StateBadge state={run.state} />
                  </td>
                  <td>{run.billed_tokens != null ? run.billed_tokens.toLocaleString() : m.runs.none}</td>
                  <td>{costCell(run, m)}</td>
                  <td>{run.minutes != null ? run.minutes.toFixed(1) : m.runs.none}</td>
                  <td className="small">
                    {run.files_changed ?? 0} ({"+"}
                    {run.lines_added ?? 0}/{"-"}
                    {run.lines_removed ?? 0})
                  </td>
                  <td>
                    <span className="badge">{m.runs.decisions[run.decision]}</span>
                  </td>
                  <td>
                    <span className="badge">{m.runs.applyStates[run.apply_state]}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selectedRun ? (
        <AdhocRunReview
          onChanged={onRunChanged}
          onRunAgain={() => runPanelRef.current?.scrollIntoView({ behavior: "smooth" })}
          run={selectedRun}
        />
      ) : null}

      <p className="muted small">
        {formatRunDate(task.created_at)} · {formatRunDate(task.updated_at)}
      </p>
    </section>
  );
}
