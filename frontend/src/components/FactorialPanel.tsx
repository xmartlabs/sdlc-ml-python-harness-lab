import { defineMessages, useMessages } from "../i18n";
import type { FactorialBlock } from "../types";

/**
 * The harness x model reading (ADR-0066): does the harness help less on a
 * stronger model? Three tables, read top to bottom — each cell's pass rate,
 * the harness effect per model with its interval, and the change in that effect
 * from the first model to the last. The sentence under them is the lab's, and
 * it never claims more than the intervals support.
 */

const MESSAGES = defineMessages({
  en: {
    title: "Harness x model",
    intro: (axis: string, cases: number) => (
      <>
        Harness factor <code>{axis}</code>, read on {cases} case(s) every cell
        scored. Effects are measured against the first harness level, per model;
        the cases are the unit of evidence, so when the harness helps some cases
        and not others, more cases narrow the intervals faster than more
        repetitions.
      </>
    ),
    harnessLevel: "Harness level",
    perPass: "per pass",
    harnessEffectPerModel: "Harness effect, per model",
    model: "Model",
    level: "Level",
    effect: "Effect",
    interval95: "95% interval",
    vs: (level: string) => `vs ${level}`,
    from: (
      fromModel: string,
      toModel: string,
      level: string,
      points: string,
      range: string,
      established: string
    ) => (
      <>
        From {fromModel} to {toModel}, the '{level}' effect changes by {points}{" "}
        {range} — {established}.
      </>
    ),
    established: "established on this corpus",
    notEstablished: "not established"
  },
  es: {
    title: "Arnés x modelo",
    intro: (axis: string, cases: number) => (
      <>
        Factor de arnés <code>{axis}</code>, leído sobre {cases} caso(s) con
        todas las celdas puntuadas. Los efectos se miden contra el primer nivel
        de arnés, por modelo; los casos son la unidad de evidencia, así que
        cuando el arnés ayuda en unos casos y no en otros, más casos achican los
        intervalos más rápido que más repeticiones.
      </>
    ),
    harnessLevel: "Nivel de arnés",
    perPass: "por éxito",
    harnessEffectPerModel: "Efecto del arnés, por modelo",
    model: "Modelo",
    level: "Nivel",
    effect: "Efecto",
    interval95: "Intervalo del 95%",
    vs: (level: string) => `vs ${level}`,
    from: (
      fromModel: string,
      toModel: string,
      level: string,
      points: string,
      range: string,
      established: string
    ) => (
      <>
        De {fromModel} a {toModel}, el efecto de '{level}' cambia {points} {range}{" "}
        — {established}.
      </>
    ),
    established: "establecido en este corpus",
    notEstablished: "no establecido"
  }
});

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
  const m = useMessages(MESSAGES);
  const cell = (level: string, model: string) =>
    block.cells.find((row) => row.level === level && row.model === model);
  const interaction = block.interaction;

  return (
    <div className="card factorial">
      <h3>{m.title}</h3>
      <p className="muted small">{m.intro(block.axis, block.paired_cases)}</p>

      <table className="data-table">
        <thead>
          <tr>
            <th>{m.harnessLevel}</th>
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
                            ? ` · ${money(row.cost_per_resolved_usd)} ${m.perPass}`
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

      <h4>{m.harnessEffectPerModel}</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>{m.model}</th>
            <th>{m.level}</th>
            <th>{m.effect}</th>
            <th>{m.interval95}</th>
          </tr>
        </thead>
        <tbody>
          {block.harness_effect.map((row) => (
            <tr key={`${row.model}-${row.level}`}>
              <td>{row.model}</td>
              <td className="mono small">
                {row.level} {m.vs(block.harness_levels[0])}
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
              {m.from(
                interaction.from_model,
                interaction.to_model,
                interaction.level,
                pp(interaction.points),
                range(interaction.ci_low, interaction.ci_high),
                interaction.established ? m.established : m.notEstablished
              )}
            </strong>
          </p>
          <p className="muted small">{block.reading}</p>
        </div>
      </div>
    </div>
  );
}
