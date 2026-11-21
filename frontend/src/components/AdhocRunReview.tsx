import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { AdhocRunEvaluation } from "./AdhocRunEvaluation";
import { DiffView } from "./DiffView";
import { ErrorBanner } from "./ErrorBanner";
import { RunProgressPanel } from "./RunProgressPanel";
import { Spinner } from "./Spinner";
import { defineMessages, useLang, useMessages } from "../i18n";
import type { AdhocApplyCheck, AdhocPatch, AdhocRunRecord } from "../types";

/**
 * Review one ad hoc run (ADR-0095): while it runs, its progress; once it
 * finishes, the patch, its flags, and the actions that decide what happens to
 * it — apply to the real folder, undo, copy the command, or decide without
 * applying.
 */

const RUNNING_STATES = new Set(["pending", "running", "scoring", "auditing"]);

const FLAG_LABELS: Record<string, { en: string; es: string }> = {
  lockfile: { en: "touches a lockfile", es: "toca un lockfile" },
  ci: { en: "touches CI config", es: "toca configuración de CI" },
  deleted: { en: "deletes a file", es: "borra un archivo" },
  binary: { en: "binary file", es: "archivo binario" },
  secret_like: { en: "looks like it holds a secret", es: "parece contener un secreto" },
  large: { en: "large change", es: "cambio grande" },
  outside_scope: { en: "outside the task's folder", es: "fuera de la carpeta de la tarea" }
};

const MESSAGES = defineMessages({
  en: {
    running: "This run is still in progress.",
    cancel: "Cancel run",
    failed: (reason: string) => `This run failed: ${reason}`,
    noPatch: "This run produced no patch.",
    purged: "Files purged — metrics only.",
    noChange: "This run left no change to review: nothing to apply. Discard it to free its files.",
    retention: (days: number) =>
      `The run's files (workspace, steps, logs) stay for the sandbox and the audit until you reject or discard it, or for ${days} days. The metrics always stay.`,
    files: "Changed files",
    openFull: "Open full run",
    actions: {
      apply: "Apply to my repo",
      applying: "Applying…",
      undo: "Undo",
      undoing: "Undoing…",
      approve: "Approve without applying",
      reject: "Reject",
      discard: "Discard",
      copyCommand: "Copy command",
      copied: "Copied",
      runAgain: "Run again with another model"
    },
    applyConfirm: (path: string) =>
      `This writes the changes into ${path} as uncommitted changes. Nothing is committed. ` +
      "You can undo it while those files are unchanged.",
    discardConfirm: "This deletes this run's files. The metrics stay. Continue?",
    blockers: "This run cannot be applied right now:",
    warnings: "Before you apply:",
    applyError: (reason: string) => `Applying failed: ${reason}`,
    rejectTitle: "Why reject this run?",
    reasons: {
      wrong_approach: "Wrong approach",
      bugs: "Has bugs",
      style: "Style does not fit",
      out_of_scope: "Out of scope",
      did_not_finish: "Did not finish",
      other: "Other"
    },
    notesLabel: "Notes (optional)",
    confirmReject: "Reject this run",
    cancelDialog: "Cancel"
  },
  es: {
    running: "Esta corrida todavía está en curso.",
    cancel: "Cancelar corrida",
    failed: (reason: string) => `Esta corrida falló: ${reason}`,
    noPatch: "Esta corrida no produjo ningún patch.",
    purged: "Archivos purgados — solo quedan las métricas.",
    noChange: "Esta corrida no dejó cambios para revisar: no hay nada que aplicar. Descartala para liberar sus archivos.",
    retention: (days: number) =>
      `Los archivos de la corrida (workspace, pasos, logs) quedan para el sandbox y la auditoría hasta que la rechazás o descartás, o por ${days} días. Las métricas quedan siempre.`,
    files: "Archivos modificados",
    openFull: "Abrir corrida completa",
    actions: {
      apply: "Aplicar a mi repo",
      applying: "Aplicando…",
      undo: "Deshacer",
      undoing: "Deshaciendo…",
      approve: "Aprobar sin aplicar",
      reject: "Rechazar",
      discard: "Descartar",
      copyCommand: "Copiar comando",
      copied: "Copiado",
      runAgain: "Correr de nuevo con otro modelo"
    },
    applyConfirm: (path: string) =>
      `Esto escribe los cambios en ${path} como cambios sin commitear. No se commitea nada. ` +
      "Podés deshacerlo mientras esos archivos no cambien.",
    discardConfirm: "Esto borra los archivos de esta corrida. Las métricas quedan. ¿Continuar?",
    blockers: "Esta corrida no se puede aplicar ahora:",
    warnings: "Antes de aplicar:",
    applyError: (reason: string) => `Falló la aplicación: ${reason}`,
    rejectTitle: "¿Por qué rechazás esta corrida?",
    reasons: {
      wrong_approach: "Enfoque equivocado",
      bugs: "Tiene bugs",
      style: "El estilo no encaja",
      out_of_scope: "Fuera de alcance",
      did_not_finish: "No terminó",
      other: "Otro"
    },
    notesLabel: "Notas (opcional)",
    confirmReject: "Rechazar esta corrida",
    cancelDialog: "Cancelar"
  }
});

function flagLabel(flag: string, lang: "en" | "es"): string {
  return FLAG_LABELS[flag]?.[lang] ?? flag;
}

export function AdhocRunReview({
  run,
  onChanged,
  onRunAgain
}: {
  run: AdhocRunRecord;
  onChanged: (updated: AdhocRunRecord) => void;
  onRunAgain: () => void;
}) {
  const m = useMessages(MESSAGES);
  const { lang } = useLang();
  const [patch, setPatch] = useState<AdhocPatch | null>(null);
  const [check, setCheck] = useState<AdhocApplyCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState(false);
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState("other");
  const [notes, setNotes] = useState("");

  const isRunning = RUNNING_STATES.has(run.state);
  const retentionDays = run.files_retention_days ?? 7;

  useEffect(() => {
    setPatch(null);
    setCheck(null);
    // A failed run is reviewed too: one cut for time often left a usable change.
    if (isRunning || run.files_purged) {
      return;
    }
    api
      .getAdhocPatch(run.run_id)
      .then(setPatch)
      .catch(() => {
        // A run with no diffable change leaves this empty.
      });
    api
      .getAdhocApplyCheck(run.run_id)
      .then(setCheck)
      .catch(() => {
        // The check is read-only context; its absence does not block review.
      });
  }, [isRunning, run.files_purged, run.run_id, run.state]);

  const poll = async (fetch: () => Promise<AdhocRunRecord>) => {
    let latest = await fetch();
    onChanged(latest);
    while (latest.apply_state === "applying" || latest.apply_state === "undoing") {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      latest = await api.getAdhocRun(run.run_id);
      onChanged(latest);
    }
  };

  const apply = async () => {
    if (!window.confirm(m.applyConfirm(run.task_id))) return;
    setBusy("apply");
    setError(null);
    try {
      await poll(() => api.applyAdhocRun(run.run_id));
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : String(applyError));
    } finally {
      setBusy("");
    }
  };

  const undo = async () => {
    setBusy("undo");
    setError(null);
    try {
      await poll(() => api.undoAdhocRun(run.run_id));
    } catch (undoError) {
      setError(undoError instanceof Error ? undoError.message : String(undoError));
    } finally {
      setBusy("");
    }
  };

  const decide = async (decision: "approved" | "rejected" | "discarded", decisionReason?: string, decisionNotes?: string) => {
    if (decision === "discarded" && !window.confirm(m.discardConfirm)) return;
    setBusy(decision);
    setError(null);
    try {
      const updated = await api.decideAdhocRun(run.run_id, {
        decision,
        reason: decisionReason,
        notes: decisionNotes
      });
      onChanged(updated);
      setShowReject(false);
    } catch (decideError) {
      setError(decideError instanceof Error ? decideError.message : String(decideError));
    } finally {
      setBusy("");
    }
  };

  const copyCommand = async () => {
    if (!patch?.apply_command) return;
    try {
      await navigator.clipboard.writeText(patch.apply_command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard denied: the command is still shown, selectable by hand.
    }
  };

  if (isRunning) {
    return (
      <div className="card">
        <RunProgressPanel live runId={run.run_id} />
        <div className="button-row">
          <button
            className="ghost"
            onClick={() =>
              void api
                .cancelRun(run.run_id)
                .then(() => api.getAdhocRun(run.run_id))
                .then(onChanged)
            }
            type="button"
          >
            {m.cancel}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      {run.state === "failed" ? <ErrorBanner message={m.failed(run.failure_reason ?? "")} /> : null}
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {run.files_purged ? (
        <p className="muted small">{m.purged}</p>
      ) : patch === null ? (
        <Spinner />
      ) : (
        <>
          {!patch.patch.trim() ? <p className="muted small">{m.noChange}</p> : null}
          <h3>{m.files}</h3>
          <ul className="diff-file-list">
            {patch.files.map((file) => (
              <li className="diff-file-row" key={file.path}>
                <span className="mono small">{file.path}</span>
                <span className="diff-file-counts">
                  <span className="diff-count diff-count--add">+{file.added}</span>
                  <span className="diff-count diff-count--del">-{file.removed}</span>
                </span>
                <span className="badge">{file.status}</span>
                {file.flags.map((flag) => (
                  <span className="badge warn" key={flag}>
                    {flagLabel(flag, lang)}
                  </span>
                ))}
              </li>
            ))}
          </ul>

          <DiffView patch={patch.patch} truncated={patch.truncated} />

          {check && check.blockers.length > 0 ? (
            <div className="callout callout--danger">
              <p>
                <strong>{m.blockers}</strong>
                <br />
                {check.blockers.join(", ")}
              </p>
            </div>
          ) : null}
          {check && check.warnings.length > 0 ? (
            <div className="callout callout--warning">
              <p>
                <strong>{m.warnings}</strong>
                <br />
                {check.warnings.join(", ")}
              </p>
            </div>
          ) : null}
          {run.apply_error ? <ErrorBanner message={m.applyError(run.apply_error)} /> : null}

          <div className="button-row">
            {run.apply_state === "applied" ? (
              <button
                className="ghost"
                disabled={busy !== ""}
                onClick={() => void undo()}
                type="button"
              >
                {busy === "undo" ? m.actions.undoing : m.actions.undo}
              </button>
            ) : (
              <button
                className="primary"
                disabled={busy !== "" || !patch.patch.trim() || (check ? !check.can_apply : false)}
                onClick={() => void apply()}
                type="button"
              >
                {busy === "apply" ? m.actions.applying : m.actions.apply}
              </button>
            )}
            <button className="ghost" onClick={() => void copyCommand()} type="button">
              {copied ? m.actions.copied : m.actions.copyCommand}
            </button>
            <button
              className="ghost"
              disabled={busy !== ""}
              onClick={() => void decide("approved")}
              type="button"
            >
              {m.actions.approve}
            </button>
            <button
              className="ghost"
              disabled={busy !== ""}
              onClick={() => setShowReject(true)}
              type="button"
            >
              {m.actions.reject}
            </button>
            <button
              className="ghost"
              disabled={busy !== ""}
              onClick={() => void decide("discarded")}
              type="button"
            >
              {m.actions.discard}
            </button>
            <button className="ghost" onClick={onRunAgain} type="button">
              {m.actions.runAgain}
            </button>
          </div>

          {showReject ? (
            <div className="card subtle">
              <h3>{m.rejectTitle}</h3>
              <label className="field">
                <select onChange={(event) => setReason(event.target.value)} value={reason}>
                  {Object.entries(m.reasons).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>{m.notesLabel}</span>
                <textarea onChange={(event) => setNotes(event.target.value)} rows={2} value={notes} />
              </label>
              <div className="button-row">
                <button
                  className="primary"
                  disabled={busy !== ""}
                  onClick={() => void decide("rejected", reason, notes)}
                  type="button"
                >
                  {m.confirmReject}
                </button>
                <button className="ghost" onClick={() => setShowReject(false)} type="button">
                  {m.cancelDialog}
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}

      <p className="muted small">{m.retention(retentionDays)}</p>
      <p className="small">
        <Link to={`/advanced/runs/${run.run_id}`}>{m.openFull}</Link>
      </p>
      {!run.files_purged ? <AdhocRunEvaluation onChanged={onChanged} run={run} /> : null}
    </div>
  );
}
