import { IconCheck, IconInfo, IconX } from "./icons";
import { defineMessages, useMessages } from "../i18n";
import type { ReadinessCheck, ReadinessReport } from "../types";

const CHECK_ICON = { ok: IconCheck, missing: IconX, unknown: IconInfo } as const;

const MESSAGES = defineMessages({
  en: {
    readFrom: (commit: string) => (
      <>
        Read from the repository at <code>{commit}</code>.{" "}
      </>
    ),
    noObservationFallback:
      "Anything the lab could not observe says so rather than being guessed at.",
    inferredTitle: "Inferred from the repository's own files",
    inferredBody:
      " — an auditor rarely knows a client's real test command, so the lab reads " +
      "the conventional markers and proposes one. Adopt it and every future run here " +
      "is scored objectively; edit it any time under the repository's configuration. " +
      "These commands execute inside the runner image, which ships Node (npm, pnpm, " +
      "yarn), Python (uv, pip) and git — Go and Rust toolchains are not installed " +
      "yet, so those suggestions need a custom runner image.",
    validationItem: (cmd: string) => (
      <>
        validation: <code>{cmd}</code>
      </>
    ),
    setupItem: (cmd: string) => (
      <>
        setup: <code>{cmd}</code>
      </>
    ),
    adopting: "Adopting…",
    adopt: "Adopt inferred commands"
  },
  es: {
    readFrom: (commit: string) => (
      <>
        Leído del repositorio en <code>{commit}</code>.{" "}
      </>
    ),
    noObservationFallback:
      "Todo lo que el lab no pudo observar se indica como tal en lugar de inventarse.",
    inferredTitle: "Inferido a partir de los propios archivos del repositorio",
    inferredBody:
      " — un auditor rara vez conoce el comando de test real del cliente, así que " +
      "el lab lee los marcadores convencionales y propone uno. Adoptalo y cada " +
      "corrida futura acá se puntúa de forma objetiva; editalo cuando quieras en la " +
      "configuración del repositorio. Estos comandos se ejecutan dentro de la imagen " +
      "del runner, que incluye Node (npm, pnpm, yarn), Python (uv, pip) y git — los " +
      "toolchains de Go y Rust todavía no están instalados, así que esas sugerencias " +
      "necesitan una imagen de runner personalizada.",
    validationItem: (cmd: string) => (
      <>
        validación: <code>{cmd}</code>
      </>
    ),
    setupItem: (cmd: string) => (
      <>
        setup: <code>{cmd}</code>
      </>
    ),
    adopting: "Adoptando…",
    adopt: "Adoptar comandos inferidos"
  }
});

/**
 * One readiness check, with the lab's claim kept as narrow as what it observed.
 *
 * Three states, not two. `unknown` exists because "the lab could not read this"
 * and "this repository does not have it" are different facts, and collapsing
 * them would let the report invent capabilities — or invent their absence.
 */
export function CheckRow({ check }: { check: ReadinessCheck }) {
  // check.label and check.detail are backend-composed and stay in English.
  const Icon = CHECK_ICON[check.status] ?? IconInfo;
  return (
    <li className={`check check--${check.status}`}>
      <span className="check-icon">
        <Icon size={16} />
      </span>
      <div>
        <strong>{check.label}</strong>
        <p className="muted">{check.detail}</p>
        {check.items.length > 0 ? (
          <ul className="check-items">
            {check.items.map((item) => (
              <li key={item}>
                <code>{item}</code>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  );
}

/** The whole report: what was detected, and what stops a run from happening. */
export function ReadinessReportView({
  report,
  onAdopt,
  adopting = false
}: {
  report: ReadinessReport;
  /**
   * Adopt the inferred commands into the target's configuration. Optional:
   * a read-only context simply shows the inference without the button.
   */
  onAdopt?: (payload: {
    validation_commands?: string[];
    setup_commands?: string[];
  }) => void;
  adopting?: boolean;
}) {
  const m = useMessages(MESSAGES);
  const gateMissing = report.checks.some(
    (check) => check.label === "Validation gate" && check.status !== "ok"
  );
  const inferredValidation = report.suggested_validation_commands ?? [];
  const inferredSetup = report.suggested_setup_commands ?? [];
  const showInference =
    gateMissing && (inferredValidation.length > 0 || inferredSetup.length > 0);
  return (
    <>
      <p className="muted">
        {m.readFrom(report.commit?.slice(0, 8) ?? report.ref)}
        {/* report.read_error is backend-composed and stays in English. */}
        {report.read_error ? report.read_error : m.noObservationFallback}
      </p>
      <ul className="check-list">
        {report.checks.map((check) => (
          <CheckRow check={check} key={check.label} />
        ))}
      </ul>
      {report.blocking.length > 0 ? (
        <ul className="blocking-list">
          {report.blocking.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {showInference ? (
        <div className="callout">
          <div>
            <strong>{m.inferredTitle}</strong>
            {m.inferredBody}
            <ul className="check-items">
              {inferredValidation.map((cmd) => (
                <li key={cmd}>{m.validationItem(cmd)}</li>
              ))}
              {inferredSetup.map((cmd) => (
                <li key={cmd}>{m.setupItem(cmd)}</li>
              ))}
            </ul>
            {onAdopt ? (
              <div className="button-row">
                <button
                  className="primary"
                  disabled={adopting}
                  onClick={() =>
                    onAdopt({
                      ...(inferredValidation.length > 0
                        ? { validation_commands: inferredValidation }
                        : {}),
                      ...(inferredSetup.length > 0
                        ? { setup_commands: inferredSetup }
                        : {})
                    })
                  }
                  type="button"
                >
                  {adopting ? m.adopting : m.adopt}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
