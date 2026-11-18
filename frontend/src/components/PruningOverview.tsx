import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { PruningOverviewRow, PruningVerdict } from "../types";
import { DECISION_CLASS, interval, RemovedCell } from "./PruningPanel";

/**
 * Harness pruning on the Improvements page (ADR-0085): every pruning
 * experiment's measured verdict, beside the model and prompt it held. These
 * are harness improvements backed by an ablation, not proposals a model
 * wrote — so they sit next to the saved proposals, not inside them.
 */

const MESSAGES = defineMessages({
  en: {
    heading: "Harness pruning",
    intro:
      "Measured, not proposed: each row ran the same cases with and without the skills and asks whether removing them costs success, progress or money. A verdict holds only for that model, prompt and set of cases.",
    empty: "No pruning experiment yet. Start one with make prune (see docs/guide-step-reward.md).",
    experiment: "Experiment",
    model: "Model · prompt",
    cases: "Cases × reps",
    removed: "Removed",
    verdict: "Verdict",
    success: "Δ success (95%)",
    cost: "Δ cost to resolve",
    why: "Why",
    running: (runs: number) => `running · ${runs} runs`,
    noVerdict: "no verdict yet",
    decision: {
      removable: "removable",
      keep: "keep",
      inconclusive: "inconclusive",
      no_signal: "no signal"
    } as Record<PruningVerdict["decision"], string>,
    next: "Next: a removable verdict feeds a skill profile (.github/skill-profiles.toml, make skill-profile-experiment); inconclusive needs more cases or repetitions; no signal needs harder cases."
  },
  es: {
    heading: "Poda del arnés",
    intro:
      "Medido, no propuesto: cada fila corrió los mismos casos con y sin las skills y pregunta si sacarlas cuesta éxito, progreso o plata. Un veredicto vale solo para ese modelo, ese prompt y esos casos.",
    empty: "Todavía no hay experimentos de poda. Arrancá uno con make prune (ver docs/guide-step-reward.md).",
    experiment: "Experimento",
    model: "Modelo · prompt",
    cases: "Casos × reps",
    removed: "Quitadas",
    verdict: "Veredicto",
    success: "Δ éxito (95%)",
    cost: "Δ costo para resolver",
    why: "Por qué",
    running: (runs: number) => `corriendo · ${runs} corridas`,
    noVerdict: "sin veredicto aún",
    decision: {
      removable: "removible",
      keep: "mantener",
      inconclusive: "no concluyente",
      no_signal: "sin señal"
    } as Record<PruningVerdict["decision"], string>,
    next: "Siguiente: un veredicto removible alimenta un perfil de skills (.github/skill-profiles.toml, make skill-profile-experiment); no concluyente necesita más casos o repeticiones; sin señal necesita casos más difíciles."
  }
});

function costDelta(verdict: PruningVerdict): string {
  const cost = verdict.metric_intervals.cost_to_resolve_usd;
  if (!cost?.interval) return "—";
  const [low, high] = cost.interval;
  const sign = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
  return `[${sign(low)}, ${sign(high)}] USD`;
}

export function PruningOverview() {
  const m = useMessages(MESSAGES);
  const [rows, setRows] = useState<PruningOverviewRow[] | null>(null);

  useEffect(() => {
    let active = true;
    api
      .listPruning()
      .then((data) => {
        if (active) setRows(data);
      })
      .catch(() => {
        if (active) setRows([]);
      });
    return () => {
      active = false;
    };
  }, []);

  if (rows === null) return null;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{m.heading}</h2>
        <span className="muted">ADR-0085</span>
      </div>
      <p className="muted small">{m.intro}</p>
      {rows.length === 0 ? (
        <p className="muted">{m.empty}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{m.experiment}</th>
              <th>{m.model}</th>
              <th>{m.cases}</th>
              <th>{m.removed}</th>
              <th>{m.verdict}</th>
              <th>{m.success}</th>
              <th>{m.cost}</th>
              <th>{m.why}</th>
            </tr>
          </thead>
          <tbody>
            {rows.flatMap((row) => {
              const head = (
                <>
                  <td>
                    <Link
                      className="mono small"
                      to={`/evaluate/experiments?experiment=${encodeURIComponent(row.experiment_id)}`}
                    >
                      {row.experiment_id}
                    </Link>
                  </td>
                  <td className="small">
                    <span className="mono">{row.model ?? "—"}</span>
                    <br />
                    <span className="muted">{row.prompt ?? "—"}</span>
                  </td>
                  <td>
                    {row.case_ids.length} × {row.repetitions}
                  </td>
                </>
              );
              if (!row.finished || row.verdicts.length === 0) {
                return [
                  <tr key={row.experiment_id}>
                    {head}
                    <td colSpan={5} className="muted small">
                      {row.finished ? m.noVerdict : m.running(row.runs)}
                    </td>
                  </tr>
                ];
              }
              return row.verdicts.map((verdict) => (
                <tr key={`${row.experiment_id}-${verdict.arm}`}>
                  {head}
                  <td>
                    <RemovedCell verdict={verdict} />
                  </td>
                  <td>
                    <span className={DECISION_CLASS[verdict.decision]}>
                      {m.decision[verdict.decision]}
                    </span>
                  </td>
                  <td className="small">{interval(verdict.success_interval)}</td>
                  <td className="small">{costDelta(verdict)}</td>
                  <td className="small">{verdict.reason}</td>
                </tr>
              ));
            })}
          </tbody>
        </table>
      )}
      <p className="muted small">{m.next}</p>
    </section>
  );
}
