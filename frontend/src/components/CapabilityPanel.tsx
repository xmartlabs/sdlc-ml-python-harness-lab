import type { CapabilityBlock, TierReading } from "../types";

/**
 * The capability frontier (ADR-0069): per arm, the highest difficulty tier it
 * does *reliably* — every case passing in at least 4 of 5 runs and the pooled
 * 95% lower bound at least 0.70 — with every lower tier reliable too. "Capable"
 * means it passed but not often enough to rely on; three runs can never show
 * more than that.
 */

const STATUS_LABEL: Record<TierReading["status"], string> = {
  reliable: "reliable",
  capable: "capable",
  not_established: "not yet"
};

const STATUS_CLASS: Record<TierReading["status"], string> = {
  reliable: "badge ok",
  capable: "badge warn",
  not_established: "badge neutral"
};

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

/** A tier with too few cases reads "too few cases", plus whether its runs held up. */
function tierLabel(reading: TierReading): string {
  if (reading.cases < MIN_CASES) {
    return reading.runs_ok ? "passing · too few cases" : "too few cases";
  }
  return STATUS_LABEL[reading.status];
}

function tierClass(reading: TierReading): string {
  if (reading.cases < MIN_CASES && reading.runs_ok) return "badge warn";
  return STATUS_CLASS[reading.status];
}

function frontierLabel(arm: CapabilityBlock["arms"][number]): string {
  if (arm.frontier !== null) return `Tier ${arm.frontier}`;
  if (Object.keys(arm.case_shortfall ?? {}).length && arm.frontier_if_enough_cases) {
    return `Tier ${arm.frontier_if_enough_cases}?`;
  }
  return "none yet";
}

const MIN_CASES = 4;

export function CapabilityPanel({ block }: { block: CapabilityBlock }) {
  const short = block.tiers.filter((tier) =>
    block.arms.some((arm) => (arm.case_shortfall ?? {})[tier] > 0)
  );
  return (
    <div className="card capability">
      <h3>Capability frontier</h3>
      <p className="muted small">
        Per arm, the highest tier done <strong>reliably</strong>: every case passing in at
        least 4 of 5 runs and the pooled 95% lower bound at least 70%, with every lower
        tier reliable too. <em>Capable</em> means it passed, but not often enough to rely
        on.
      </p>
      {short.length > 0 && (
        <div className="callout callout--warning small">
          <p>
            {short.length === block.tiers.length
              ? "Every tier"
              : `Tier ${short.join(", ")}`}{" "}
            has fewer than {MIN_CASES} cases, so no frontier can be established yet. A
            frontier marked <strong>?</strong> is where the runs so far point; add cases
            tagged <code>tier:&lt;name&gt;</code> to turn it into a reading.
          </p>
        </div>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>Arm</th>
            <th>Frontier</th>
            {block.tiers.map((tier) => (
              <th key={tier}>Tier {tier}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.arms.map((arm) => (
            <tr key={arm.label}>
              <td className="mono small">{arm.label}</td>
              <td>
                <strong>{frontierLabel(arm)}</strong>
              </td>
              {block.tiers.map((tier) => {
                const reading = arm.tiers[tier];
                return (
                  <td key={tier} title={reading?.reason}>
                    {reading ? (
                      <>
                        <span className={tierClass(reading)}>{tierLabel(reading)}</span>{" "}
                        <span className="muted small">
                          {reading.passed}/{reading.runs} · ≥{pct(reading.wilson_low)}
                        </span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Hover a cell for the reason. Failures from a broken model-harness conversation — a
        malformed tool call, a context overflow — are a serving or harness fix, not a
        limit of the model; check the run log before ruling a small model out.
      </p>
    </div>
  );
}
