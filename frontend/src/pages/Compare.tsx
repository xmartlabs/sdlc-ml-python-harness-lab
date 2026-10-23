import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { CompareAuditPanel } from "../components/CompareAuditPanel";
import { EmptyState } from "../components/EmptyState";
import { HelpPanel } from "../components/HelpPanel";
import { StateBadge } from "../components/StateBadge";
import { IconAlert } from "../components/icons";
import { defineMessages, pickMessages, useMessages } from "../i18n";
import type { RunRecord, ScoreRecord, ValidationRecord } from "../types";

type CompareProps = {
  runs: RunRecord[];
};

// States a run can be in once it has stopped executing. A run awaiting its
// manual score has a validation and a cost record; it is finished as far as the
// reading is concerned, even if nobody has graded it. Mirrors
// `experiment_audit_service.TERMINAL_STATES`, which gates the same request
// server-side.
const TERMINAL_STATES = new Set([
  "completed",
  "failed",
  "cancelled",
  "awaiting_manual_score"
]);

// The runner command is built by BenchRunAdapter with a stable flag layout, so
// case / prompt / models can be recovered from it without a config endpoint.
type RunFlags = {
  caseId: string;
  prompt: string;
  plannerModel: string;
  executorModel: string;
  maxSteps: string;
  // Three counts, never one. Removing a skill, adding a staged one and asking
  // the agent to invoke one are three different conditions, and two of them
  // point in opposite directions: an arm with 24 skills removed is the bare
  // control, an arm with 24 asked for is the fullest treatment. Collapsing them
  // into a single direction-less number rendered those two identically, which
  // reads as the exact inverse of what happened. Availability is not use
  // either, so the third count stands on its own (ADR-0041).
  skillsRemoved: number;
  skillsAdded: number;
  skillsAsked: number;
};

function flagValue(command: string[], flag: string): string {
  const idx = command.indexOf(flag);
  return idx >= 0 && idx + 1 < command.length ? command[idx + 1] : "";
}

function countFlag(command: string[], flag: string): number {
  return command.filter((token) => token === flag).length;
}

function parseFlags(command: string[]): RunFlags {
  const m = pickMessages(MESSAGES);
  return {
    caseId: flagValue(command, "--case") || "—",
    prompt: flagValue(command, "--prompt") || "—",
    plannerModel: flagValue(command, "--planner-model") || m.none,
    executorModel: flagValue(command, "--executor-model") || "—",
    maxSteps: flagValue(command, "--max-steps") || "—",
    skillsRemoved: countFlag(command, "--disable-skill"),
    skillsAdded: countFlag(command, "--extra-skill"),
    skillsAsked: countFlag(command, "--invoke-skill"),
  };
}

// A skill count always carries its unit: "24" beside "none" is only readable
// when the row label says which direction the 24 points in.
function skillCount(value: number): string {
  const m = pickMessages(MESSAGES);
  return value ? m.skillUnit(value) : m.none;
}

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? String(value) : value;
}

type Column = {
  run: RunRecord;
  flags: RunFlags;
  validation: ValidationRecord | null;
  // Registry scores, keyed by name. These carry direction and provenance, so a
  // metric the comparison has never seen still knows which way is better.
  scores: Record<string, ScoreRecord>;
};

const MESSAGES = defineMessages({
  en: {
    none: "none",
    pass: "pass",
    fail: "fail",
    skillUnit: (count: number) => `${count} skill(s)`,
    // Mirrors ProvenanceBadge's three kinds — this table renders the raw
    // provenance value inline rather than as a badge.
    provenanceLabel: { fact: "fact", observation: "observation", judgement: "judgement" } as Record<
      string,
      string
    >,
    metrics: {
      case: "Case",
      executorModel: "Executor model",
      plannerModel: "Planner model",
      promptVariant: "Prompt variant",
      maxSteps: "Max steps",
      skillsRemoved: "Skills removed",
      skillsAdded: "Skills added",
      skillsAskedFor: "Skills asked for",
      objective: "Objective",
      finalScore: "Final score",
      composite: "Composite",
      tokens: "Tokens",
      costUsd: "Cost (USD)",
      timeMinutes: "Time (min)",
      steps: "Steps",
      interventions: "Interventions"
    },
    eyebrow: "Advanced",
    title: "Advanced Compare",
    lede: "Line up runs of the same case across models or harness conditions — objective score, cost, and effort side by side.",
    adhocWarning: (link: ReactNode) => (
      <>
        <strong>Ad-hoc comparison.</strong> No causal claim is implied unless the
        conditions satisfy an experiment design. These runs were not necessarily
        matched on repository, model or repetition count, so a difference here can
        come from anything that differed between them. To make a claim, run an{" "}
        {link}.
      </>
    ),
    experimentLink: "experiment",
    helpTitle: "How to read this comparison",
    help1: (sameCase: ReactNode) => (
      <>Pick 2–4 runs of the {sameCase} (different models or skill conditions) from the selectors.</>
    ),
    sameCase: "same case",
    help2: "The metrics table highlights the best cell per row — higher is better for scores, lower for tokens / cost / time / steps.",
    help3: (
      <>
        The three <strong>skill rows</strong> are three conditions, not one.{" "}
        <em>Removed</em> is an ablation, <em>added</em> is a staged skill, and{" "}
        <em>asked for</em> is the prompt telling the agent to invoke them. A
        column reading &ldquo;24 skill(s) removed&rdquo; is the bare arm, not
        the equipped one.
      </>
    ),
    help4: (objective: ReactNode) => (
      <>{objective} comes from automatic validation; SWE-bench resolves to pass/fail, other cases show their check summary.</>
    ),
    help5: "The aggregated Leaderboard (% resolved per harness + model + prompt + harness version) lives on Benchmarks.",
    noRunsTitle: "No runs to compare yet.",
    noRunsDescription: "Create at least one run from the Runs page to start comparing outcomes.",
    remove: "Remove",
    addRun: "+ Add run",
    metricHeader: "Metric",
    scoreRegistryNote:
      "Rows below the divider come from the score registry. Their “best” cell follows each metric's declared direction, not the sign — a lower cost wins, a lower pass rate does not. A judgement is a model's opinion and is marked as one; it should not be read beside a fact as the same kind of claim.",
    leaderboardMoved: (link: ReactNode) => (
      <>Looking for the aggregated ranking by condition (% resolved, harness, version)? It moved to the {link}.</>
    ),
    benchmarksLink: "Benchmarks"
  },
  es: {
    none: "ninguno",
    pass: "pasó",
    fail: "falló",
    skillUnit: (count: number) => `${count} skill(s)`,
    provenanceLabel: { fact: "hecho", observation: "observación", judgement: "juicio" } as Record<
      string,
      string
    >,
    metrics: {
      case: "Caso",
      executorModel: "Modelo ejecutor",
      plannerModel: "Modelo planificador",
      promptVariant: "Variante de prompt",
      maxSteps: "Máximo de pasos",
      skillsRemoved: "Skills quitadas",
      skillsAdded: "Skills agregadas",
      skillsAskedFor: "Skills pedidas",
      objective: "Objetivo",
      finalScore: "Puntaje final",
      composite: "Compuesto",
      tokens: "Tokens",
      costUsd: "Costo (USD)",
      timeMinutes: "Tiempo (min)",
      steps: "Pasos",
      interventions: "Intervenciones"
    },
    eyebrow: "Avanzado",
    title: "Comparar (avanzado)",
    lede: "Alineá corridas del mismo caso entre modelos o condiciones de arnés — puntaje objetivo, costo y esfuerzo lado a lado.",
    adhocWarning: (link: ReactNode) => (
      <>
        <strong>Comparación ad hoc.</strong> No se implica ninguna afirmación causal
        a menos que las condiciones cumplan un diseño de experimento. Estas corridas
        no fueron necesariamente emparejadas en repositorio, modelo o cantidad de
        repeticiones, así que una diferencia acá puede venir de cualquier cosa que
        haya diferido entre ellas. Para afirmar algo, corré un {link}.
      </>
    ),
    experimentLink: "experimento",
    helpTitle: "Cómo leer esta comparación",
    help1: (sameCase: ReactNode) => (
      <>Elegí entre 2 y 4 corridas del {sameCase} (distintos modelos o condiciones de skills) desde los selectores.</>
    ),
    sameCase: "mismo caso",
    help2: "La tabla de métricas resalta la mejor celda por fila — más alto es mejor para los puntajes, más bajo para tokens / costo / tiempo / pasos.",
    help3: (
      <>
        Las tres <strong>filas de skills</strong> son tres condiciones, no una.{" "}
        <em>Quitadas</em> es una ablación, <em>agregadas</em> es una skill staged,
        y <em>pedidas</em> es el prompt que le dice al agente que las invoque. Una
        columna que dice &ldquo;24 skill(s) removed&rdquo; es el brazo desnudo, no
        el equipado.
      </>
    ),
    help4: (objective: ReactNode) => (
      <>{objective} viene de la validación automática; SWE-bench resuelve a pasó/falló, otros casos muestran su resumen de chequeo.</>
    ),
    help5: "El Leaderboard agregado (% resuelto por arnés + modelo + prompt + versión de arnés) vive en Benchmarks.",
    noRunsTitle: "Todavía no hay corridas para comparar.",
    noRunsDescription: "Creá al menos una corrida desde la página de Corridas para empezar a comparar resultados.",
    remove: "Quitar",
    addRun: "+ Agregar corrida",
    metricHeader: "Métrica",
    scoreRegistryNote:
      "Las filas debajo del separador vienen del registro de puntajes. Su celda “mejor” sigue la dirección declarada de cada métrica, no el signo — un costo más bajo gana, una tasa de éxito más baja no. Un juicio es la opinión de un modelo y está marcado como tal; no debería leerse junto a un hecho como el mismo tipo de afirmación.",
    leaderboardMoved: (link: ReactNode) => (
      <>¿Buscás el ranking agregado por condición (% resuelto, arnés, versión)? Se mudó a {link}.</>
    ),
    benchmarksLink: "Benchmarks"
  }
});

type Messages = typeof MESSAGES.en;

// One comparison row: how to read the display + optional numeric for best-cell.
type Metric = {
  label: string;
  display: (col: Column) => string;
  numeric?: (col: Column) => number | null;
  better?: "high" | "low";
  // Set for registry rows: a fact, an observation and a judgement are three
  // different kinds of claim, and a comparison table is exactly where that
  // distinction is easiest to lose.
  provenance?: string;
};

// Built inside the component from the current language's labels — the table
// is otherwise identical in shape to a static array.
function buildMetrics(m: Messages): Metric[] {
  return [
    { label: m.metrics.case, display: (c) => c.flags.caseId },
    { label: m.metrics.executorModel, display: (c) => c.flags.executorModel },
    { label: m.metrics.plannerModel, display: (c) => c.flags.plannerModel },
    { label: m.metrics.promptVariant, display: (c) => c.flags.prompt },
    { label: m.metrics.maxSteps, display: (c) => c.flags.maxSteps },
    {
      label: m.metrics.skillsRemoved,
      display: (c) => skillCount(c.flags.skillsRemoved),
    },
    {
      label: m.metrics.skillsAdded,
      display: (c) => skillCount(c.flags.skillsAdded),
    },
    {
      label: m.metrics.skillsAskedFor,
      display: (c) => skillCount(c.flags.skillsAsked),
    },
    {
      label: m.metrics.objective,
      display: (c) =>
        c.validation?.objective?.summary ??
        (c.validation?.objective?.passed === true
          ? m.pass
          : c.validation?.objective?.passed === false
            ? m.fail
            : "—"),
      numeric: (c) =>
        c.validation?.objective?.passed === true
          ? 1
          : c.validation?.objective?.passed === false
            ? 0
            : null,
      better: "high",
    },
    {
      label: m.metrics.finalScore,
      display: (c) => fmt(c.run.score),
      numeric: (c) => c.run.score ?? null,
      better: "high",
    },
    {
      label: m.metrics.composite,
      display: (c) => fmt(c.validation?.composite),
      numeric: (c) => c.validation?.composite ?? null,
      better: "high",
    },
    {
      label: m.metrics.tokens,
      display: (c) => fmt(c.validation?.cost?.total_tokens),
      numeric: (c) => c.validation?.cost?.total_tokens ?? null,
      better: "low",
    },
    {
      label: m.metrics.costUsd,
      display: (c) => fmt(c.validation?.cost?.cost_usd),
      numeric: (c) => c.validation?.cost?.cost_usd ?? null,
      better: "low",
    },
    {
      label: m.metrics.timeMinutes,
      display: (c) => fmt(c.validation?.cost?.time_minutes),
      numeric: (c) => c.validation?.cost?.time_minutes ?? null,
      better: "low",
    },
    {
      label: m.metrics.steps,
      display: (c) => fmt(c.validation?.effort?.steps),
      numeric: (c) => c.validation?.effort?.steps ?? null,
      better: "low",
    },
    {
      label: m.metrics.interventions,
      display: (c) => fmt(c.run.interventions),
      numeric: (c) => c.run.interventions ?? null,
      better: "low",
    },
  ];
}

// Index of the winning column for a metric (or null when there is no clear win).
function bestIndex(metric: Metric, columns: Column[]): number | null {
  if (!metric.numeric || !metric.better) return null;
  let best: number | null = null;
  let bestVal = metric.better === "high" ? -Infinity : Infinity;
  const nums = columns.map((c) => metric.numeric!(c));
  const present = nums.filter((n): n is number => n !== null);
  if (present.length < 2 || new Set(present).size === 1) return null;
  nums.forEach((n, i) => {
    if (n === null) return;
    if ((metric.better === "high" && n > bestVal) || (metric.better === "low" && n < bestVal)) {
      bestVal = n;
      best = i;
    }
  });
  return best;
}

export function Compare({ runs }: CompareProps) {
  const m = useMessages(MESSAGES);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [scoresByRun, setScoresByRun] = useState<
    Record<string, Record<string, ScoreRecord>>
  >({});
  // name → direction, from the registry. Not stored on a score row: direction
  // is a property of the definition, and duplicating it per value is how the
  // two drift apart.
  const [directions, setDirections] = useState<Record<string, string>>({});

  useEffect(() => {
    api
      .getScoreDefinitions()
      .then((payload) => {
        const map: Record<string, string> = {};
        for (const group of Object.values(payload.categories)) {
          for (const definition of group) {
            map[definition.name] = definition.direction;
          }
        }
        setDirections(map);
      })
      .catch(() => setDirections({}));
  }, []);
  const [validations, setValidations] = useState<Record<string, ValidationRecord | null>>({});

  // Default to the two most recent runs once the list arrives.
  useEffect(() => {
    if (selectedIds.length === 0 && runs.length > 0) {
      setSelectedIds(runs.slice(0, Math.min(2, runs.length)).map((r) => r.run_id));
    }
  }, [runs, selectedIds.length]);

  // Fetch validation for any selected run we have not loaded yet.
  useEffect(() => {
    let active = true;
    for (const id of selectedIds) {
      if (id in validations) continue;
      api
        .getValidation(id)
        .then((v) => active && setValidations((prev) => ({ ...prev, [id]: v })))
        .catch(() => active && setValidations((prev) => ({ ...prev, [id]: null })));
    }
    return () => {
      active = false;
    };
  }, [selectedIds, validations]);

  // Same, for the registry scores.
  useEffect(() => {
    let active = true;
    for (const id of selectedIds) {
      if (id in scoresByRun) continue;
      api
        .listRunScores(id)
        .then(
          (rows) =>
            active &&
            setScoresByRun((prev) => ({
              ...prev,
              [id]: Object.fromEntries(rows.map((row) => [row.name, row]))
            }))
        )
        .catch(
          () => active && setScoresByRun((prev) => ({ ...prev, [id]: {} }))
        );
    }
    return () => {
      active = false;
    };
  }, [selectedIds, scoresByRun]);

  const columns: Column[] = useMemo(
    () =>
      selectedIds
        .map((id) => runs.find((r) => r.run_id === id))
        .filter((r): r is RunRecord => Boolean(r))
        .map((run) => ({
          run,
          flags: parseFlags(run.command),
          validation: validations[run.run_id] ?? null,
          scores: scoresByRun[run.run_id] ?? {}
        })),
    [selectedIds, runs, validations, scoresByRun]
  );

  // The reading describes an outcome, so it waits for every run to have one.
  const allFinished = useMemo(
    () =>
      columns.length > 0 &&
      columns.every((col) => TERMINAL_STATES.has(col.run.state)),
    [columns]
  );

  // Every registry metric present on any selected run, so a comparison never
  // silently drops a score one arm recorded and the other did not.
  const scoreRows: Metric[] = useMemo(() => {
    const seen = new Map<string, ScoreRecord>();
    for (const column of columns) {
      for (const score of Object.values(column.scores)) {
        if (!seen.has(score.name)) seen.set(score.name, score);
      }
    }
    return [...seen.values()]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((sample) => ({
        label: `${sample.name}`,
        provenance: sample.provenance,
        display: (column: Column) => {
          const score = column.scores[sample.name];
          if (!score) return "—";
          if (score.value_bool !== null) return score.value_bool ? m.pass : m.fail;
          if (score.value_num !== null) {
            return String(Number(score.value_num.toPrecision(4)));
          }
          return score.value_text ?? "—";
        },
        numeric: (column: Column) => {
          const score = column.scores[sample.name];
          if (!score) return null;
          if (score.value_bool !== null) return score.value_bool ? 1 : 0;
          return score.value_num;
        },
        // Direction comes from the registry, never from the sign: a drop in
        // cost is a win, a drop in pass rate is not.
        better:
          directions[sample.name] === "higher_is_better"
            ? ("high" as const)
            : directions[sample.name] === "lower_is_better"
              ? ("low" as const)
              : undefined
      }));
  }, [columns, directions, m]);

  const metrics = useMemo(() => buildMetrics(m), [m]);

  const setColumn = (index: number, runId: string) =>
    setSelectedIds((prev) => prev.map((id, i) => (i === index ? runId : id)));
  const addColumn = () => {
    const next = runs.find((r) => !selectedIds.includes(r.run_id)) ?? runs[0];
    if (next) setSelectedIds((prev) => [...prev, next.run_id]);
  };
  const removeColumn = (index: number) =>
    setSelectedIds((prev) => prev.filter((_, i) => i !== index));

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{m.eyebrow}</p>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      <div className="callout callout--warning">
        <IconAlert size={16} />
        <p>{m.adhocWarning(<Link to="/evaluate/new">{m.experimentLink}</Link>)}</p>
      </div>

      <HelpPanel title={m.helpTitle} docHref="docs/guide-web-ui.md">
        <ol className="skills-howto">
          <li>{m.help1(<strong>{m.sameCase}</strong>)}</li>
          <li>{m.help2}</li>
          <li>{m.help3}</li>
          <li>{m.help4(<strong>{m.metrics.objective}</strong>)}</li>
          <li>{m.help5}</li>
        </ol>
      </HelpPanel>

      {runs.length === 0 ? (
        <EmptyState description={m.noRunsDescription} title={m.noRunsTitle} />
      ) : (
        <>
          <div className="compare-picker">
            {selectedIds.map((id, index) => (
              <div className="compare-picker-col" key={index}>
                <select value={id} onChange={(e) => setColumn(index, e.target.value)}>
                  {runs.map((run) => (
                    <option key={run.run_id} value={run.run_id}>
                      {run.run_id}
                    </option>
                  ))}
                </select>
                {selectedIds.length > 2 ? (
                  <button className="ghost" onClick={() => removeColumn(index)} type="button">
                    {m.remove}
                  </button>
                ) : null}
              </div>
            ))}
            {selectedIds.length < 4 && selectedIds.length < runs.length ? (
              <button className="ghost" onClick={addColumn} type="button">
                {m.addRun}
              </button>
            ) : null}
          </div>

          <div className="table-scroll">
            <table className="compare-table">
              <thead>
                <tr>
                  <th>{m.metricHeader}</th>
                  {columns.map((col) => (
                    <th key={col.run.run_id}>
                      <div className="compare-col-head">
                        <span>{col.run.run_id}</span>
                        <StateBadge state={col.run.state} />
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...metrics, ...scoreRows].map((metric) => {
                  const winner = bestIndex(metric, columns);
                  return (
                    <tr key={metric.label}>
                      <th scope="row">
                        {metric.label}
                        {metric.provenance && (
                          <div
                            className={`small provenance-${metric.provenance}`}
                          >
                            {m.provenanceLabel[metric.provenance] ?? metric.provenance}
                          </div>
                        )}
                      </th>
                      {columns.map((col, i) => (
                        <td className={winner === i ? "compare-best" : undefined} key={col.run.run_id}>
                          {metric.display(col)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <CompareAuditPanel allFinished={allFinished} runIds={selectedIds} />

          {scoreRows.length > 0 && <p className="muted small">{m.scoreRegistryNote}</p>}

          <p className="muted">
            {m.leaderboardMoved(<Link to="/advanced/benchmarks">{m.benchmarksLink}</Link>)}
          </p>
        </>
      )}
    </section>
  );
}
