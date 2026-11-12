import { useEffect, useState } from "react";

import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import { InfoTooltip } from "./InfoTooltip";
import type { StepOutcome, StepRewardRecord } from "../types";

const MESSAGES = defineMessages({
  en: {
    title: "Per-step reward",
    explainer:
      "Inspired by reinforcement learning: the case's own checks run on the " +
      "workspace after every step, and each step is credited with the progress " +
      "it made. The agent never sees these numbers — they are computed after " +
      "the run.",
    none:
      "No per-step record for this run. Runs started before per-step snapshots " +
      "existed, or whose case has no executable checks, have only the final " +
      "verdict.",
    resolvedAt: "Resolved at step",
    neverResolved: "Never fully resolved",
    costToResolve: "Cost to resolve",
    afterResolution: "Spent after resolution",
    progressAuc: "Progress AUC",
    regressions: "Regressions",
    step: "Step",
    progress: "Checks passing",
    reward: "Reward",
    cost: "Cost",
    consulted: "Harness opened",
    notMeasured: "not measured",
    regression: "regression",
    afterResolvedNote: "after resolution",
    creditTitle: "What was in context when progress happened",
    creditNote:
      "Co-occurrence, not cause. The agent might have made the same progress " +
      "without the item; only an ablation (make prune) can tell.",
    item: "Item",
    rewardWhenConsulted: "Reward in those steps",
    steps: "Steps",
    auc: "Mean progress over the steps: 1.0 is solved in step one and kept.",
    afterTip:
      "Share of the run's spend after every check already passed: re-reading, " +
      "extra tests, scope creep. Tokens are used when the model was free.",
    tokensBasis: "of tokens"
  },
  es: {
    title: "Recompensa por paso",
    explainer:
      "Inspirado en aprendizaje por refuerzo: los checks del propio caso se " +
      "corren sobre el workspace después de cada paso, y a cada paso se le " +
      "acredita el progreso que hizo. El agente nunca ve estos números: se " +
      "calculan después de la corrida.",
    none:
      "Esta corrida no tiene registro por paso. Las corridas anteriores a las " +
      "fotos por paso, o cuyo caso no tiene checks ejecutables, solo tienen el " +
      "veredicto final.",
    resolvedAt: "Resuelto en el paso",
    neverResolved: "Nunca se resolvió del todo",
    costToResolve: "Costo hasta resolver",
    afterResolution: "Gastado después de resolver",
    progressAuc: "AUC de progreso",
    regressions: "Regresiones",
    step: "Paso",
    progress: "Checks que pasan",
    reward: "Recompensa",
    cost: "Costo",
    consulted: "Arnés consultado",
    notMeasured: "sin medir",
    regression: "regresión",
    afterResolvedNote: "después de resolver",
    creditTitle: "Qué estaba en contexto cuando hubo progreso",
    creditNote:
      "Co-ocurrencia, no causa. El agente quizás habría progresado igual sin " +
      "ese ítem; solo una ablación (make prune) lo puede decir.",
    item: "Ítem",
    rewardWhenConsulted: "Recompensa en esos pasos",
    steps: "Pasos",
    auc: "Progreso medio a lo largo de los pasos: 1.0 es resuelto en el paso uno y mantenido.",
    afterTip:
      "Parte del gasto de la corrida posterior a que todos los checks ya " +
      "pasaban: releer, tests extra, alcance de más. Si el modelo era gratis se " +
      "usan tokens.",
    tokensBasis: "de tokens"
  }
});

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

function usd(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `$${value.toFixed(2)}`;
}

function signed(value: number | null): string {
  if (value === null) return "—";
  const points = Math.round(value * 100);
  return points > 0 ? `+${points}` : `${points}`;
}

function ProgressBar({ step }: { step: StepOutcome }) {
  const value = step.progress ?? 0;
  return (
    <div
      aria-hidden="true"
      className="step-reward-track"
      title={step.total ? `${step.passed}/${step.total}` : undefined}
    >
      <div className="step-reward-fill" style={{ width: `${value * 100}%` }} />
    </div>
  );
}

export function StepRewardPanel({ runId }: { runId: string }) {
  const m = useMessages(MESSAGES);
  const [record, setRecord] = useState<StepRewardRecord | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setLoaded(false);
    api
      .getStepReward(runId)
      .then(setRecord)
      .catch(() => setRecord(null))
      .finally(() => setLoaded(true));
  }, [runId]);

  if (!loaded) return null;

  const header = (
    <div className="row space-between">
      <h3>
        {m.title} <InfoTooltip text={m.explainer} />
      </h3>
    </div>
  );

  if (record === null || !record.measured) {
    return (
      <section className="panel">
        {header}
        <p className="muted">{m.none}</p>
      </section>
    );
  }

  const share =
    record.post_resolution_cost_share ?? record.post_resolution_token_share;
  const shareIsTokens =
    record.post_resolution_cost_share === null &&
    record.post_resolution_token_share !== null;
  const resolvedAt = record.steps_to_resolve;

  return (
    <section className="panel">
      {header}
      <div className="step-reward-tiles">
        <div className="step-reward-tile">
          <span className="muted small">
            {resolvedAt !== null ? m.resolvedAt : m.neverResolved}
          </span>
          <strong>
            {resolvedAt !== null ? `${resolvedAt} / ${record.steps.length}` : "—"}
          </strong>
        </div>
        <div className="step-reward-tile">
          <span className="muted small">{m.costToResolve}</span>
          <strong>{usd(record.cost_to_resolve_usd)}</strong>
        </div>
        <div className="step-reward-tile">
          <span className="muted small">
            {m.afterResolution} <InfoTooltip text={m.afterTip} />
          </span>
          <strong>
            {percent(share)}
            {shareIsTokens && <span className="muted small"> {m.tokensBasis}</span>}
          </strong>
        </div>
        <div className="step-reward-tile">
          <span className="muted small">
            {m.progressAuc} <InfoTooltip text={m.auc} />
          </span>
          <strong>{record.progress_auc?.toFixed(2) ?? "—"}</strong>
        </div>
        <div className="step-reward-tile">
          <span className="muted small">{m.regressions}</span>
          <strong>{record.step_regressions}</strong>
        </div>
      </div>

      <table className="data-table step-reward-table">
        <thead>
          <tr>
            <th>{m.step}</th>
            <th>{m.progress}</th>
            <th>{m.reward}</th>
            <th>{m.cost}</th>
            <th>{m.consulted}</th>
          </tr>
        </thead>
        <tbody>
          {record.steps.map((step) => {
            const afterResolution = resolvedAt !== null && step.step > resolvedAt;
            const regressed = step.reward !== null && step.reward < 0;
            return (
              <tr
                className={afterResolution ? "step-reward-after" : undefined}
                key={step.step}
              >
                <td className="mono">
                  {step.step}
                  {afterResolution && (
                    <span className="muted small"> · {m.afterResolvedNote}</span>
                  )}
                </td>
                <td>
                  {step.progress === null ? (
                    <span className="muted small">{m.notMeasured}</span>
                  ) : (
                    <div className="step-reward-progress">
                      <ProgressBar step={step} />
                      <span className="mono small">
                        {step.total ? `${step.passed}/${step.total}` : percent(step.progress)}
                      </span>
                    </div>
                  )}
                </td>
                <td className="mono">
                  {signed(step.reward)}
                  {regressed && (
                    <span className="step-reward-regression small">
                      {" "}
                      ▼ {m.regression}
                    </span>
                  )}
                </td>
                <td className="mono small">{usd(step.cost_usd)}</td>
                <td className="small">
                  {step.consulted.length ? (
                    step.consulted.map((item) => (
                      <span className="chip mono" key={item}>
                        {item}
                      </span>
                    ))
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {record.credit.length > 0 && (
        <>
          <h4>{m.creditTitle}</h4>
          <p className="muted small">{m.creditNote}</p>
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.item}</th>
                <th>{m.steps}</th>
                <th>{m.rewardWhenConsulted}</th>
              </tr>
            </thead>
            <tbody>
              {record.credit.map((row) => (
                <tr key={row.item}>
                  <td className="mono small">{row.item}</td>
                  <td className="mono small">{row.steps.join(", ")}</td>
                  <td className="mono">{signed(row.reward_when_consulted)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
