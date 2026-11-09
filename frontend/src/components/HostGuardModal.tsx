import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { defineMessages, useMessages } from "../i18n";
import type { LocalActiveRunRef, LocalGuardReport } from "../types";

export type HostGuardAction = "load" | "unload" | "tests" | "check";

const MESSAGES = defineMessages({
  en: {
    title: "This host is busy with a lab run",
    runningHeading: "Running",
    queuedHeading: "Queued",
    none: "None",
    copy: {
      load: "Loading this model can evict the model those runs use, or starve them of memory — they can fail or slow down, distorting the experiment.",
      unload: "Unloading this model can evict the model those runs use, or starve them of memory — they can fail or slow down, distorting the experiment.",
      tests: "Tests share the GPU with these runs — both the tests and the runs will be slower.",
      check: "Checking this configuration can load a model on this host, which can evict the model those runs use or starve them of memory."
    } as Record<HostGuardAction, string>,
    routingUnknown:
      "Gateway runs on this host could not be checked (the gateway did not answer).",
    continueNote:
      "Continue anyway records an event on the affected runs, so an ignored warning stays visible when the experiment is read later.",
    cancel: "Cancel",
    continueAnyway: "Continue anyway",
    continuing: "Continuing…",
    runLink: (runId: string) => `Run ${runId}`
  },
  es: {
    title: "Este host está ocupado con una corrida del lab",
    runningHeading: "En curso",
    queuedHeading: "En cola",
    none: "Ninguna",
    copy: {
      load: "Cargar este modelo puede desalojar el modelo que esas corridas usan, o dejarlas sin memoria — pueden fallar o volverse más lentas, distorsionando el experimento.",
      unload: "Descargar este modelo puede desalojar el modelo que esas corridas usan, o dejarlas sin memoria — pueden fallar o volverse más lentas, distorsionando el experimento.",
      tests: "Las pruebas comparten la GPU con esas corridas — tanto las pruebas como las corridas serán más lentas.",
      check: "Revisar esta configuración puede cargar un modelo en este host, lo que puede desalojar el modelo que esas corridas usan o dejarlas sin memoria."
    } as Record<HostGuardAction, string>,
    routingUnknown:
      "No se pudieron revisar las corridas del gateway en este host (el gateway no respondió).",
    continueNote:
      "Continuar de todos modos registra un evento en las corridas afectadas, así una advertencia ignorada sigue visible al leer el experimento después.",
    cancel: "Cancelar",
    continueAnyway: "Continuar de todos modos",
    continuing: "Continuando…",
    runLink: (runId: string) => `Corrida ${runId}`
  }
});

function RunList({ runs, heading }: { runs: LocalActiveRunRef[]; heading: string }) {
  const m = useMessages(MESSAGES);
  return (
    <div>
      <p className="muted small">{heading}</p>
      {runs.length === 0 ? (
        <p className="muted small">{m.none}</p>
      ) : (
        <ul>
          {runs.map((run) => (
            <li key={run.run_id}>
              <Link to={`/advanced/runs/${run.run_id}`}>{m.runLink(run.run_id)}</Link>{" "}
              <span className="muted small">
                ({run.model}
                {run.state ? ` · ${run.state}` : ""})
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type HostGuardModalProps = {
  guard: LocalGuardReport;
  action: HostGuardAction;
  onCancel: () => void;
  onContinue: () => void | Promise<unknown>;
};

/**
 * The host guard's confirmation dialog, shared by every page that can load,
 * unload or test a serving host a lab run may be using, including New
 * evaluation's own *Check configuration* preflight (`action: "check"`).
 */
export function HostGuardModal({ guard, action, onCancel, onContinue }: HostGuardModalProps) {
  const m = useMessages(MESSAGES);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [continuing, setContinuing] = useState(false);

  useEffect(() => {
    dialogRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCancel();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  const handleContinue = () => {
    if (continuing) {
      return;
    }
    setContinuing(true);
    // The parent clears `guard` on both success and failure, which unmounts
    // this modal — `setContinuing(false)` after that would be a no-op on an
    // unmounted component, not a leak, so no cleanup flag is needed here.
    void Promise.resolve(onContinue()).finally(() => setContinuing(false));
  };

  return (
    <div className="modal-overlay">
      <div
        aria-labelledby="host-guard-modal-title"
        aria-modal="true"
        className="modal-dialog"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <h2 id="host-guard-modal-title">{m.title}</h2>
        <p className="muted small">{guard.host}</p>
        <p>{m.copy[action]}</p>
        {guard.routing_unknown ? <p className="muted small">{m.routingUnknown}</p> : null}
        <div className="form-grid">
          <RunList heading={m.runningHeading} runs={guard.running} />
          <RunList heading={m.queuedHeading} runs={guard.queued} />
        </div>
        <p className="muted small">{m.continueNote}</p>
        <div className="toolbar">
          <button className="secondary" disabled={continuing} onClick={onCancel} type="button">
            {m.cancel}
          </button>
          <button className="primary" disabled={continuing} onClick={handleContinue} type="button">
            {continuing ? m.continuing : m.continueAnyway}
          </button>
        </div>
      </div>
    </div>
  );
}
