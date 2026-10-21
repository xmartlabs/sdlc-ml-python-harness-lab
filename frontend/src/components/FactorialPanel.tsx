import type { FactorialBlock } from "../types";

/**
 * The harness x model reading (ADR-0066): does the harness help less on a
 * stronger model? Three tables, read top to bottom — each cell's pass rate,
 * the harness effect per model with its interval, and the change in that effect
 * from the first model to the last. The sentence under them is the lab's, and
 * it never claims more than the intervals support.
 */

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function pp(value: number | null): string {
  if (value === null) return "—";
  const rounded = Math.round(value);
  return `${rounded > 0 ? "+" : ""}${rounded} pp`;
}

function range(low: number | null, high: number | null): string {
  if (low === null || high === null) return "";
  return `[${pp(low)}, ${pp(high)}]`;
}

function money(value: number | null): string {
  return value === null ? "—" : `$${value.toFixed(2)}`;
}

export function FactorialPanel({ block }: { block: FactorialBlock }) {
  const cell = (level: string, model: string) =>
    block.cells.find((row) => row.level === level && row.model === model);
  const interaction = block.interaction;

  return (
    <div className="card factorial">
      <h3>Harness x model</h3>
      <p className="muted small">
        Harness factor <code>{block.axis}</code>, read on {block.paired_cases} case(s)
        every cell scored. Effects are measured against the first harness level, per
        model; the cases are the unit of evidence, so more cases narrow the intervals
        faster than more repetitions.
      </p>

      <table className="data-table">
        <thead>
          <tr>
            <th>Harness level</th>
            {block.models.map((model) => (
              <th key={model}>{model}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.harness_levels.map((level) => (
            <tr key={level}>
              <td className="mono small">{level}</td>
              {block.models.map((model) => {
                const row = cell(level, model);
                return (
                  <td key={model}>
                    {row && row.runs > 0 ? (
                      <>
                        <strong>{pct(row.pass_rate)}</strong>{" "}
                        <span className="muted small">
                          {row.passed}/{row.runs}
                          {row.cost_per_resolved_usd !== null
                            ? ` · ${money(row.cost_per_resolved_usd)} per pass`
                            : ""}
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

      <h4>Harness effect, per model</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Level</th>
            <th>Effect</th>
            <th>95% interval</th>
          </tr>
        </thead>
        <tbody>
          {block.harness_effect.map((row) => (
            <tr key={`${row.model}-${row.level}`}>
              <td>{row.model}</td>
              <td className="mono small">
                {row.level} vs {block.harness_levels[0]}
              </td>
              <td>
                <strong>{pp(row.points)}</strong>
              </td>
              <td className="muted small">{range(row.ci_low, row.ci_high)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div
        className={`callout ${interaction.established ? "callout--ok" : "callout--warning"}`}
      >
        <div>
          <p>
            <strong>
              From {interaction.from_model} to {interaction.to_model}, the '
              {interaction.level}' effect changes by {pp(interaction.points)}{" "}
              {range(interaction.ci_low, interaction.ci_high)} —{" "}
              {interaction.established ? "established on this corpus" : "not established"}.
            </strong>
          </p>
          <p className="muted small">{block.reading}</p>
        </div>
      </div>
    </div>
  );
}
