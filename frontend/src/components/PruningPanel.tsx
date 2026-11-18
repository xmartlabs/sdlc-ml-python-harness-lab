import { defineMessages, useMessages } from "../i18n";
import type { PruningVerdict } from "../types";

/**
 * The pruning verdict (ADR-0076, ADR-0085): per treatment arm, can the skills
 * it removed go? Non-inferiority, not "no difference seen": an item is
 * removable only when every interval stays inside the margin declared up
 * front; one that straddles a margin is inconclusive — run more cases.
 */

const MESSAGES = defineMessages({
  en: {
    title: "Pruning verdict",
    intro: (margin: string) => (
      <>
        Quality first: the harness must add on top of the model. Any drop in
        success without these skills means <strong>keep</strong>. Both arms
        passing (or failing) everything is <strong>no signal</strong> — the
        cases are too easy or too hard to show what the skills add. Only with
        no drop, a signal, and every 95% interval inside its margin (success{" "}
        {margin}, cost and progress the relative margin) is it{" "}
        <strong>removable</strong>.
      </>
    ),
    decision: {
      removable: "removable",
      keep: "keep",
      inconclusive: "inconclusive",
      no_signal: "no signal"
    } as Record<PruningVerdict["decision"], string>,
    arm: "Arm",
    removed: "Removed",
    verdict: "Verdict",
    success: "Δ success (95%)",
    cases: "Paired cases",
    why: "Why",
    skills: (n: number) => `${n} skills`,
    missing: (names: string) => `missing: ${names}`,
    footer:
      "Harness value depends on the model: read this as a verdict for this model and these cases only. A group verdict of removable lets the whole group go for that tier; keep or inconclusive sends you back to leave-one-out (make prune ITEMS=…) to find which member matters."
  },
  es: {
    title: "Veredicto de poda",
    intro: (margin: string) => (
      <>
        Primero la calidad: el arnés tiene que sumar por encima del modelo.
        Cualquier caída de éxito sin estas skills es <strong>mantener</strong>.
        Si ambos brazos pasan (o fallan) todo es <strong>sin señal</strong> —
        los casos son demasiado fáciles o difíciles para mostrar lo que suman.
        Solo sin caída, con señal y con cada intervalo del 95% dentro de su
        margen (éxito {margin}, costo y progreso el margen relativo) es{" "}
        <strong>removible</strong>.
      </>
    ),
    decision: {
      removable: "removible",
      keep: "mantener",
      inconclusive: "no concluyente",
      no_signal: "sin señal"
    } as Record<PruningVerdict["decision"], string>,
    arm: "Brazo",
    removed: "Quitadas",
    verdict: "Veredicto",
    success: "Δ éxito (95%)",
    cases: "Casos pareados",
    why: "Por qué",
    skills: (n: number) => `${n} skills`,
    missing: (names: string) => `faltan: ${names}`,
    footer:
      "El valor del arnés depende del modelo: leé esto como un veredicto para este modelo y estos casos. Un grupo removible puede irse entero para ese nivel de modelo; mantener o no concluyente te manda de vuelta a leave-one-out (make prune ITEMS=…) para encontrar qué miembro importa."
  }
});

export const DECISION_CLASS: Record<PruningVerdict["decision"], string> = {
  removable: "badge ok",
  keep: "badge error",
  inconclusive: "badge warn",
  no_signal: "badge neutral"
};

function points(value: number): string {
  const rounded = Math.round(value * 100);
  return `${rounded > 0 ? "+" : ""}${rounded}`;
}

export function interval(range: [number, number] | null): string {
  return range === null ? "—" : `[${points(range[0])}, ${points(range[1])}] pts`;
}

export function RemovedCell({ verdict }: { verdict: PruningVerdict }) {
  const m = useMessages(MESSAGES);
  if (verdict.removed.length <= 1) {
    return <span className="mono small">{verdict.removed[0] ?? verdict.item}</span>;
  }
  return (
    <details>
      <summary className="small">{m.skills(verdict.removed.length)}</summary>
      <ul className="mono small">
        {verdict.removed.map((name) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
    </details>
  );
}

export function PruningPanel({ verdicts }: { verdicts: PruningVerdict[] }) {
  const m = useMessages(MESSAGES);
  const successMargin = verdicts[0]?.margins.success_rate;
  return (
    <div className="card pruning">
      <h3>{m.title}</h3>
      <p className="muted small">
        {m.intro(successMargin === undefined ? "—" : `${Math.round(successMargin * 100)} pts`)}
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th>{m.arm}</th>
            <th>{m.removed}</th>
            <th>{m.verdict}</th>
            <th>{m.success}</th>
            <th>{m.cases}</th>
            <th>{m.why}</th>
          </tr>
        </thead>
        <tbody>
          {verdicts.map((verdict) => (
            <tr key={verdict.arm}>
              <td className="mono small">{verdict.arm}</td>
              <td>
                <RemovedCell verdict={verdict} />
              </td>
              <td>
                <span className={DECISION_CLASS[verdict.decision]}>
                  {m.decision[verdict.decision]}
                </span>
              </td>
              <td className="small">{interval(verdict.success_interval)}</td>
              <td>{verdict.paired_cases}</td>
              <td className="small">
                {verdict.reason}
                {verdict.missing_metrics.length > 0 ? (
                  <span className="muted"> ({m.missing(verdict.missing_metrics.join(", "))})</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{m.footer}</p>
    </div>
  );
}
