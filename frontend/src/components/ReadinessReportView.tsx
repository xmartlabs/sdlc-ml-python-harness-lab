import { IconCheck, IconInfo, IconX } from "./icons";
import type { ReadinessCheck, ReadinessReport } from "../types";

const CHECK_ICON = { ok: IconCheck, missing: IconX, unknown: IconInfo } as const;

/**
 * One readiness check, with the lab's claim kept as narrow as what it observed.
 *
 * Three states, not two. `unknown` exists because "the lab could not read this"
 * and "this repository does not have it" are different facts, and collapsing
 * them would let the report invent capabilities — or invent their absence.
 */
export function CheckRow({ check }: { check: ReadinessCheck }) {
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
        Read from the repository at{" "}
        <code>{report.commit?.slice(0, 8) ?? report.ref}</code>.{" "}
        {report.read_error
          ? report.read_error
          : "Anything the lab could not observe says so rather than being guessed at."}
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
            <strong>Inferred from the repository&apos;s own files</strong> — an
            auditor rarely knows a client&apos;s real test command, so the lab
            reads the conventional markers and proposes one. Adopt it and every
            future run here is scored objectively; edit it any time under the
            repository&apos;s configuration. These commands execute inside the
            runner image, which ships Node (npm, pnpm, yarn), Python (uv, pip)
            and git — Go and Rust toolchains are not installed yet, so those
            suggestions need a custom runner image.
            <ul className="check-items">
              {inferredValidation.map((cmd) => (
                <li key={cmd}>
                  validation: <code>{cmd}</code>
                </li>
              ))}
              {inferredSetup.map((cmd) => (
                <li key={cmd}>
                  setup: <code>{cmd}</code>
                </li>
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
                  {adopting ? "Adopting…" : "Adopt inferred commands"}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
