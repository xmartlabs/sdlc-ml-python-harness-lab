import { useEffect, useMemo, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { Link } from "react-router-dom";

import { defineMessages, useMessages } from "../../i18n";
import { InfoTooltip } from "../InfoTooltip";

/**
 * A guided flow (ADR-0093): one question per step, answered with buttons, and a
 * flow diagram beside it that lights up what was chosen and marks what is next.
 *
 * A flow only declares its steps. This shell draws the diagram, the trail of
 * answers (each one a way back), the step itself, and the review screen that
 * shows the run count and the estimated cost before anything is created. It
 * never creates or launches: the flow's `onCreate` does, and only when asked.
 */

export type GuideOption = {
  id: string;
  label: string;
  sub?: string;
  /** Long text, in the ⓘ — the screen keeps the label and one line. */
  tip?: string;
  /** A short status pill: what exists today vs what is new. */
  tag?: { tone: "ok" | "warn" | "new"; text: string };
};

export type GuideStep = {
  key: string;
  /** The diagram node's title. */
  node: string;
  question: string;
  hint?: string;
  kind: "one" | "many" | "reps";
  /** Options, given what was chosen before. */
  options?: (values: GuideValues) => GuideOption[];
  /** With kind "many": how many must be picked. */
  min?: number;
  /** Skip this step for these values (it is not drawn either). */
  skip?: (values: GuideValues) => boolean;
};

export type GuideValues = Record<string, string | string[] | number | undefined>;

export type GuideEstimate = {
  arms: number;
  runs: number;
  /** null when this case and model have no measured run yet. */
  costUsd: number | null;
  minutes: number | null;
  /** How many runs the median stands on. */
  basis: number;
  notes?: string[];
};

export type GuideCreated = { ids: string[]; reused?: string[]; warnings?: string[] };

type GuideFlowProps = {
  title: string;
  steps: GuideStep[];
  initial?: GuideValues;
  /** Async: it may read the lab's real medians. Called once all steps are answered. */
  estimate: (values: GuideValues) => Promise<GuideEstimate>;
  /** What the result will be, one line. */
  outcome: string;
  /** Terminal equivalent, shown folded. */
  cli?: (values: GuideValues) => string;
  /** Extra content on the review screen (e.g. a reuse toggle). */
  reviewExtra?: (values: GuideValues, set: (key: string, value: GuideValues[string]) => void) => ReactNode;
  onCreate: (values: GuideValues) => Promise<GuideCreated>;
  /** Where a created experiment is opened. */
  openPath?: (id: string) => string;
  onRestart?: () => void;
};

const MESSAGES = defineMessages({
  en: {
    step: (n: number, total: number) => `Step ${n} of ${total}`,
    lastStep: "Last step",
    done: "Done",
    review: "Review before creating",
    created: "Created",
    chosen: "chosen",
    next: "next",
    available: "available",
    continueWith: (n: number) => `Continue with ${n}`,
    pickAtLeast: (n: number) => `Pick at least ${n}`,
    perArm: "Repetitions per arm",
    repsValue: (n: number) => `${n} per arm`,
    perArmTip:
      "1 to 10. One is exploratory; read a difference only from 5 per arm — a one-run lead reverses easily.",
    less: "Fewer",
    more: "More",
    toReview: "Review",
    arms: "arms",
    runs: "runs",
    cost: "estimated cost",
    time: "time, one at a time",
    basis: (n: number) => `Estimated from the lab's median for this case and model (n=${n}).`,
    noBasis: "No measured run of this case with this model yet: the first run tells the cost.",
    many: (n: number) => `That is ${n} runs. Check it is what you want before creating.`,
    create: (n: number) => `Create (${n} runs)`,
    creating: "Creating…",
    createdNote:
      "Created, not launched. Launch it from Experiments, behind the usual launch form and preflight.",
    reusedNote: (ids: string) => `Reused, already measured: ${ids}.`,
    open: "Open it to launch",
    restart: "Start another",
    cli: "Terminal equivalent",
    goBack: (node: string) => `Back to ${node}`,
    estimating: "Estimating…"
  },
  es: {
    step: (n: number, total: number) => `Paso ${n} de ${total}`,
    lastStep: "Último paso",
    done: "Listo",
    review: "Revisá antes de crear",
    created: "Creado",
    chosen: "elegido",
    next: "siguiente",
    available: "disponible",
    continueWith: (n: number) => `Seguir con ${n}`,
    pickAtLeast: (n: number) => `Elegí al menos ${n}`,
    perArm: "Repeticiones por brazo",
    repsValue: (n: number) => `${n} por brazo`,
    perArmTip:
      "De 1 a 10. Con 1 es exploratorio; leé una diferencia recién con 5 por brazo: una ventaja de una corrida se da vuelta fácil.",
    less: "Menos",
    more: "Más",
    toReview: "Revisar",
    arms: "brazos",
    runs: "corridas",
    cost: "costo estimado",
    time: "tiempo, de a una",
    basis: (n: number) => `Estimado con la mediana real del lab para este caso y modelo (n=${n}).`,
    noBasis: "No hay corridas medidas de este caso con este modelo: la primera dice el costo.",
    many: (n: number) => `Son ${n} corridas. Revisá que sea lo que querés antes de crear.`,
    create: (n: number) => `Crear (${n} corridas)`,
    creating: "Creando…",
    createdNote:
      "Creado, no lanzado. Se lanza desde Experimentos, con el formulario y el preflight de siempre.",
    reusedNote: (ids: string) => `Reusado, ya medido: ${ids}.`,
    open: "Abrilo para lanzarlo",
    restart: "Empezar otro",
    cli: "Equivalente en terminal",
    goBack: (node: string) => `Volver a ${node}`,
    estimating: "Estimando…"
  }
});

export function money(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function duration(minutes: number): string {
  if (minutes < 60) return `${Math.max(1, Math.round(minutes))} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${Math.round(minutes - hours * 60)} min`;
}

function answered(step: GuideStep, values: GuideValues, confirmed: Set<string>): boolean {
  const value = values[step.key];
  if (step.kind === "many") {
    return Array.isArray(value) && value.length >= (step.min ?? 1) && confirmed.has(step.key);
  }
  if (step.kind === "reps") return typeof value === "number";
  return value !== undefined && value !== "";
}

function labelFor(step: GuideStep, values: GuideValues): string {
  const value = values[step.key];
  if (value === undefined) return "";
  if (step.kind === "reps") return String(value);
  const options = step.options?.(values) ?? [];
  const name = (id: string) => options.find((option) => option.id === id)?.label ?? id;
  return Array.isArray(value) ? value.map(name).join(" · ") : name(String(value));
}

/** The flow diagram: one node per step, then review and result. */
function FlowMap({
  steps,
  values,
  confirmed,
  current,
  reviewed,
  created,
  onJump,
  m
}: {
  steps: GuideStep[];
  values: GuideValues;
  confirmed: Set<string>;
  current: number;
  reviewed: boolean;
  created: boolean;
  onJump: (index: number) => void;
  m: (typeof MESSAGES)["en"];
}) {
  const nodes = [
    ...steps.map((step, index) => ({
      key: step.key,
      title: step.node,
      value: answered(step, values, confirmed)
        ? step.kind === "reps"
          ? m.repsValue(Number(values[step.key]))
          : labelFor(step, values)
        : "",
      on: answered(step, values, confirmed),
      next: index === current,
      index
    })),
    { key: "__review", title: m.review, value: "", on: reviewed, next: current === steps.length && !reviewed, index: -1 },
    { key: "__out", title: m.created, value: "", on: created, next: false, index: -1 }
  ];
  const rowH = 62;
  const height = 16 + nodes.length * rowH;
  const onKey = (event: KeyboardEvent<SVGGElement>, index: number) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onJump(index);
    }
  };
  return (
    <svg aria-label="flow" className="guide-map" role="img" viewBox={`0 0 520 ${height}`}>
      {nodes.map((node, i) => {
        const y = 16 + i * rowH;
        const clickable = node.index >= 0 && (node.on || node.next);
        const className = [
          "guide-node",
          node.on ? "is-on" : node.next ? "is-next" : i > current + 1 && !node.on ? "is-dim" : "",
          clickable ? "is-clickable" : ""
        ]
          .filter(Boolean)
          .join(" ");
        const value = node.value.length > 42 ? `${node.value.slice(0, 40)}…` : node.value;
        return (
          <g key={node.key}>
            {i > 0 ? (
              <path className={node.on ? "guide-edge is-on" : "guide-edge"} d={`M260 ${y - 10} L260 ${y + 4}`} />
            ) : null}
            <g
              aria-label={clickable ? m.goBack(node.title) : undefined}
              className={className}
              onClick={clickable ? () => onJump(node.index) : undefined}
              onKeyDown={clickable ? (event) => onKey(event, node.index) : undefined}
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
            >
              <rect height={46} rx={9} width={300} x={110} y={y + 4} />
              <text x={260} y={value ? y + 21 : y + 28}>
                {node.title}
              </text>
              {value ? (
                <text className="guide-node-sub" x={260} y={y + 38}>
                  {value}
                </text>
              ) : null}
            </g>
          </g>
        );
      })}
    </svg>
  );
}

export function GuideFlow({
  title,
  steps: allSteps,
  initial,
  estimate,
  outcome,
  cli,
  reviewExtra,
  onCreate,
  openPath = (id) => `/evaluate/experiments?experiment=${encodeURIComponent(id)}`,
  onRestart
}: GuideFlowProps) {
  const m = useMessages(MESSAGES);
  const [values, setValues] = useState<GuideValues>(initial ?? {});
  const [confirmed, setConfirmed] = useState<Set<string>>(() => new Set(Object.keys(initial ?? {})));
  const [repsDraft, setRepsDraft] = useState(3);
  const [estimateValue, setEstimateValue] = useState<GuideEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<GuideCreated | null>(null);

  // Keep in step with a new `initial` (a deep link from the showcase).
  useEffect(() => {
    setValues(initial ?? {});
    setConfirmed(new Set(Object.keys(initial ?? {})));
    setCreated(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(initial ?? {})]);

  const steps = useMemo(() => allSteps.filter((step) => !step.skip?.(values)), [allSteps, values]);
  const current = steps.findIndex((step) => !answered(step, values, confirmed));
  const index = current === -1 ? steps.length : current;
  const complete = index >= steps.length;

  useEffect(() => {
    if (!complete) {
      setEstimateValue(null);
      return;
    }
    let active = true;
    estimate(values)
      .then((result) => active && setEstimateValue(result))
      .catch(() => active && setEstimateValue(null));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [complete, JSON.stringify(values)]);

  const set = (key: string, value: GuideValues[string]) => {
    setCreated(null);
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const clearFrom = (from: number) => {
    const drop = new Set(steps.slice(from).map((step) => step.key));
    setValues((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => !drop.has(key))));
    setConfirmed((prev) => new Set([...prev].filter((key) => !drop.has(key))));
    setCreated(null);
    setError(null);
  };

  const create = () => {
    setBusy(true);
    setError(null);
    onCreate(values)
      .then(setCreated)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const step = complete ? null : steps[index];

  return (
    <div className="guide-body">
      <div className="guide-main">
        <nav aria-label={title} className="guide-trail">
          <span className="guide-trail-root">{title}</span>
          {steps.map((s, i) =>
            answered(s, values, confirmed) ? (
              <button key={s.key} onClick={() => clearFrom(i)} type="button">
                {s.kind === "reps" ? `${values[s.key]}×` : labelFor(s, values)}
              </button>
            ) : null
          )}
        </nav>

        {step ? (
          <section aria-live="polite" className="guide-step">
            <span className="guide-step-label">{m.step(index + 1, steps.length)}</span>
            <h2>{step.question}</h2>
            {step.hint ? <p className="muted">{step.hint}</p> : null}
            {step.kind === "reps" ? (
              <div className="guide-reps-wrap">
                <span className="guide-field-label">
                  {m.perArm} <InfoTooltip text={m.perArmTip} />
                </span>
                <div className="guide-reps">
                  <button aria-label={m.less} onClick={() => setRepsDraft((n) => Math.max(1, n - 1))} type="button">
                    −
                  </button>
                  <output aria-live="polite">{repsDraft}</output>
                  <button aria-label={m.more} onClick={() => setRepsDraft((n) => Math.min(10, n + 1))} type="button">
                    +
                  </button>
                </div>
                <div className="guide-actions">
                  <button className="primary" onClick={() => set(step.key, repsDraft)} type="button">
                    {m.toReview}
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="guide-options">
                  {(step.options?.(values) ?? []).map((option) => {
                    const chosen = values[step.key];
                    const pressed = Array.isArray(chosen) ? chosen.includes(option.id) : chosen === option.id;
                    return (
                      // A card, not a button: the ⓘ is a button of its own and
                      // a button may not hold another one (invalid HTML).
                      <div className="guide-option" data-pressed={pressed} key={option.id}>
                        <button
                          aria-pressed={pressed}
                          className="guide-option-hit"
                          onClick={() => {
                          if (step.kind === "many") {
                            const list = Array.isArray(chosen) ? [...chosen] : [];
                            const at = list.indexOf(option.id);
                            if (at >= 0) list.splice(at, 1);
                            else list.push(option.id);
                            setConfirmed((prev) => {
                              const next = new Set(prev);
                              next.delete(step.key);
                              return next;
                            });
                            set(step.key, list);
                          } else {
                            set(step.key, option.id);
                          }
                        }}
                          type="button"
                        >
                          <strong>{option.label}</strong>
                          {option.sub ? <span className="muted">{option.sub}</span> : null}
                          {option.tag ? (
                            <span className={`guide-tag guide-tag--${option.tag.tone}`}>{option.tag.text}</span>
                          ) : null}
                        </button>
                        {option.tip ? (
                          <span className="guide-option-info">
                            <InfoTooltip text={option.tip} />
                          </span>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                {step.kind === "many" ? (
                  <div className="guide-actions">
                    {(() => {
                      const count = Array.isArray(values[step.key]) ? (values[step.key] as string[]).length : 0;
                      const enough = count >= (step.min ?? 1);
                      return (
                        <button
                          className="primary"
                          disabled={!enough}
                          onClick={() => setConfirmed((prev) => new Set(prev).add(step.key))}
                          type="button"
                        >
                          {enough ? m.continueWith(count) : m.pickAtLeast(step.min ?? 1)}
                        </button>
                      );
                    })()}
                  </div>
                ) : null}
              </>
            )}
          </section>
        ) : (
          <section aria-live="polite" className="guide-step">
            <span className="guide-step-label">{created ? m.done : m.lastStep}</span>
            <h2>{created ? m.created : m.review}</h2>
            <div className="guide-review">
              <dl>
                {steps.map((s) => (
                  <div key={s.key}>
                    <dt>{s.node}</dt>
                    <dd>{s.kind === "reps" ? `${values[s.key]}` : labelFor(s, values)}</dd>
                  </div>
                ))}
              </dl>
              {estimateValue ? (
                <>
                  <div className="guide-totals">
                    <div>
                      <strong>{estimateValue.arms}</strong>
                      <span>{m.arms}</span>
                    </div>
                    <div>
                      <strong>{estimateValue.runs}</strong>
                      <span>{m.runs}</span>
                    </div>
                    <div>
                      <strong>{estimateValue.costUsd === null ? "—" : `~${money(estimateValue.costUsd)}`}</strong>
                      <span>{m.cost}</span>
                    </div>
                    <div>
                      <strong>{estimateValue.minutes === null ? "—" : `~${duration(estimateValue.minutes)}`}</strong>
                      <span>{m.time}</span>
                    </div>
                  </div>
                  <p className="muted guide-small">
                    {estimateValue.costUsd === null ? m.noBasis : m.basis(estimateValue.basis)}
                  </p>
                  {estimateValue.notes?.map((note) => (
                    <p className="guide-warn" key={note}>
                      {note}
                    </p>
                  ))}
                  {estimateValue.runs > 12 ? <p className="guide-warn">{m.many(estimateValue.runs)}</p> : null}
                </>
              ) : (
                <p className="muted guide-small">{m.estimating}</p>
              )}
              {reviewExtra ? reviewExtra(values, set) : null}
            </div>
            <p className="muted">{outcome}</p>
            {error ? <p className="guide-error">{error}</p> : null}
            {created ? (
              <div className="callout callout--ok">
                <p>{m.createdNote}</p>
                {created.reused && created.reused.length > 0 ? <p>{m.reusedNote(created.reused.join(", "))}</p> : null}
                {created.warnings?.map((warning) => (
                  <p className="muted" key={warning}>
                    {warning}
                  </p>
                ))}
                {created.ids.map((id) => (
                  <p key={id}>
                    <Link to={openPath(id)}>
                      {m.open}: {id}
                    </Link>
                  </p>
                ))}
              </div>
            ) : null}
            <div className="guide-actions">
              {!created ? (
                <button
                  className="primary"
                  disabled={busy || !estimateValue || estimateValue.runs === 0}
                  onClick={create}
                  type="button"
                >
                  {busy ? m.creating : m.create(estimateValue?.runs ?? 0)}
                </button>
              ) : null}
              <button
                className="secondary"
                onClick={() => {
                  clearFrom(0);
                  onRestart?.();
                }}
                type="button"
              >
                {m.restart}
              </button>
            </div>
            {cli ? (
              <details className="guide-cli">
                <summary className="muted">{m.cli}</summary>
                <pre>
                  <code>{cli(values)}</code>
                </pre>
              </details>
            ) : null}
          </section>
        )}
      </div>
      <aside className="guide-aside">
        <div className="guide-aside-inner">
          <div className="guide-legend">
            <span>
              <i className="is-on" />
              {m.chosen}
            </span>
            <span>
              <i className="is-next" />
              {m.next}
            </span>
            <span>
              <i />
              {m.available}
            </span>
          </div>
          <FlowMap
            confirmed={confirmed}
            created={!!created}
            current={index}
            m={m}
            onJump={clearFrom}
            reviewed={complete}
            steps={steps}
            values={values}
          />
        </div>
      </aside>
    </div>
  );
}
