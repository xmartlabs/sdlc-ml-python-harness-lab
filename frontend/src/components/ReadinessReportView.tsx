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
export function ReadinessReportView({ report }: { report: ReadinessReport }) {
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
    </>
  );
}
