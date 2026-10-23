import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import { Link } from "react-router-dom";

import { defineMessages, useMessages } from "../i18n";
import type {
  CaseRecord,
  LeaderboardRunRow,
  OfficialReferenceRow,
  OverviewReport,
  RunRecord,
  ScoreAggregate
} from "../types";

type DashboardProps = {
  cases: CaseRecord[];
  runs: RunRecord[];
  apiStatus: string;
};

const ALL = "all";

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(0)}%`;
}

function usd(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `$${value.toFixed(2)}`;
}

const MESSAGES = defineMessages({
  en: {
    trend: {
      notEnough: "Not enough weeks with a validated run to draw a trend yet.",
      ariaLabel: "Weekly success rate",
      tooltip: (weekEnding: string, rate: string, n: number) =>
        `${weekEnding}: ${rate} over ${n} run${n === 1 ? "" : "s"}`
    },
    harnessSignal: {
      heading: "Harness signal",
      noScores:
        "No scores recorded yet. Runs are scored against the registry when they finish; older runs can be scored from their detail page.",
      overRuns: (n: number) => `over ${n} scored run${n === 1 ? "" : "s"}`,
      skillAdherence: "Skill adherence",
      shareJudgedUsed: "share of the harness judged used · judgement",
      itemsUseful: "Items useful / run",
      judgedRedundant: (n: string) => `${n} judged redundant`,
      diffSize: "Diff size",
      lines: (n: number) => `${n} lines`,
      medianObservation: "median · observation",
      boundaryViolations: "Boundary violations",
      forbiddenWorst: "forbidden paths touched, worst run",
      lowAdherenceWarning: (
        <>
          Agents are judged to use under half of the harness available to them. That is a
          finding about the <em>harness</em>, not the model: an item nothing ever reaches is
          cost without effect. Read the per-item verdicts in a run&apos;s{" "}
          <strong>Harness catalog</strong>, then ablate the ones that never earn their keep and
          measure the difference.
        </>
      )
    },
    overview: {
      harnessVersion: "Harness version",
      successRate: (days: number) => `Success rate (${days}d)`,
      overValidated: (n: number) => `over ${n} validated run${n === 1 ? "" : "s"}`,
      regressions: "Regressions",
      ofCasesHistory: (n: number) => `of ${n} case${n === 1 ? "" : "s"} with a history`,
      medianCost: "Median cost",
      perSuccess: (cost: string) => `${cost} per success`,
      regressionSuite: "Regression suite",
      ofActiveCases: (n: number) => `of ${n} active case${n === 1 ? "" : "s"}`,
      experiments: "Experiments",
      thisWindowTotal: (n: number) => `this window · ${n} total`,
      qualityOverTime: "Quality over time",
      weeklyShareDesc:
        "Weekly share of objectively-validated runs that passed. A week with no validated run is left blank rather than drawn as zero.",
      worthAttention: "Worth attention",
      regressedSince: "Regressed since a previous pass:",
      unscoredRuns: (n: number) =>
        `${n} finished run${n === 1 ? "" : "s"} have no scores yet — scoring is on demand, so they are absent from every experiment summary.`,
      openFindings: (n: number, link: ReactNode) => (
        <>
          {n} open {link} with nothing testing them yet.
        </>
      ),
      findingsLink: (n: number) => `finding${n === 1 ? "" : "s"}`,
      noRegressionGate:
        "No case carries its own objective validation, so nothing here can gate a change to the harness.",
      nothingOutstanding: "Nothing outstanding."
    },
    page: {
      eyebrow: "Benchmarks",
      title: "Harness quality",
      lede: "Is the harness getting better, is anything regressing, and what does a success cost. Every figure carries its denominator — a rate over four runs and one over four hundred are different claims.",
      cases: "Cases",
      runs: "Runs",
      running: "Running",
      completed: "Completed"
    },
    leaderboard: {
      heading: "Leaderboard — ranking by condition",
      conditionsRuns: (conditions: number, runs: number) =>
        `${conditions} condition${conditions === 1 ? "" : "s"} · ${runs} run${runs === 1 ? "" : "s"}`,
      filterLane: "Filter by lane",
      laneAll: "lane: all",
      filterHarness: "Filter by harness",
      harnessAll: "harness: all",
      filterModel: "Filter by model",
      modelAll: "model: all",
      filterPrompt: "Filter by prompt",
      promptAll: "prompt: all",
      filterVersion: "Filter by harness version",
      versionAll: "version: all",
      officialTooltip:
        "Reference rows from the official leaderboard (data/reference/swebench_official.json).",
      officialReference: "official reference",
      continuationsTooltip:
        "Runs continued from another run (ADR-0026). A human read the workspace and wrote a new prompt, so they are not comparable to one-shot attempts.",
      includeContinuations: "include continuations",
      noRuns: "No terminal runs yet — launch a case from the Runs page to populate the ranking.",
      colRank: "#",
      colCondition: "Condition",
      colPctResolved: "% resolved",
      colRuns: "Runs",
      colAvgScore: "Avg score",
      colBest: "Best",
      colAvgCost: "Avg cost",
      officialBadge: "official",
      officialBadgeTooltip:
        "Reference row from the official leaderboard — different subset and infrastructure, indicative comparison only.",
      source: "source ↗",
      officialPctTooltip: "Official pass@1 over the 500 Verified instances",
      resolvedTooltip: (count: number, total: number) =>
        `${count}/${total} objectively-validated runs resolved`,
      resolvedCount: (count: number, total: number) => `${count}/${total} resolved`,
      footerNote: (
        <>
          ⚠️ Indicative comparison: your % runs over the lab&apos;s curated subset and your
          conditions; official rows are pass@1 over the 500 SWE-bench Verified instances. Update
          the reference by editing <code>data/reference/swebench_official.json</code>.
        </>
      )
    }
  },
  es: {
    trend: {
      notEnough: "Todavía no hay suficientes semanas con una corrida validada para trazar una tendencia.",
      ariaLabel: "Tasa de éxito semanal",
      tooltip: (weekEnding: string, rate: string, n: number) =>
        `${weekEnding}: ${rate} sobre ${n} corrida${n === 1 ? "" : "s"}`
    },
    harnessSignal: {
      heading: "Señal del arnés",
      noScores:
        "Todavía no hay puntajes registrados. Las corridas se puntúan contra el registro cuando terminan; las corridas anteriores se pueden puntuar desde su página de detalle.",
      overRuns: (n: number) => `sobre ${n} corrida${n === 1 ? "" : "s"} puntuada${n === 1 ? "" : "s"}`,
      skillAdherence: "Adherencia a skills",
      shareJudgedUsed: "parte del arnés juzgada como usada · juicio",
      itemsUseful: "Ítems útiles / corrida",
      judgedRedundant: (n: string) => `${n} juzgados redundantes`,
      diffSize: "Tamaño del diff",
      lines: (n: number) => `${n} líneas`,
      medianObservation: "mediana · observación",
      boundaryViolations: "Violaciones de límites",
      forbiddenWorst: "rutas prohibidas tocadas, peor corrida",
      lowAdherenceWarning: (
        <>
          Se juzga que los agentes usan menos de la mitad del arnés disponible. Eso es un
          hallazgo sobre el <em>arnés</em>, no sobre el modelo: un ítem al que nunca se llega es
          costo sin efecto. Leé los veredictos por ítem en el <strong>catálogo de arnés</strong>{" "}
          de una corrida, después quitá los que nunca se ganan su lugar y medí la diferencia.
        </>
      )
    },
    overview: {
      harnessVersion: "Versión del arnés",
      successRate: (days: number) => `Tasa de éxito (${days}d)`,
      overValidated: (n: number) => `sobre ${n} corrida${n === 1 ? "" : "s"} validada${n === 1 ? "" : "s"}`,
      regressions: "Regresiones",
      ofCasesHistory: (n: number) => `de ${n} caso${n === 1 ? "" : "s"} con historial`,
      medianCost: "Costo mediano",
      perSuccess: (cost: string) => `${cost} por éxito`,
      regressionSuite: "Suite de regresión",
      ofActiveCases: (n: number) => `de ${n} caso${n === 1 ? "" : "s"} activo${n === 1 ? "" : "s"}`,
      experiments: "Experimentos",
      thisWindowTotal: (n: number) => `esta ventana · ${n} en total`,
      qualityOverTime: "Calidad a lo largo del tiempo",
      weeklyShareDesc:
        "Parte semanal de corridas objetivamente validadas que pasaron. Una semana sin corrida validada queda en blanco en lugar de dibujarse como cero.",
      worthAttention: "Vale la pena atender",
      regressedSince: "Regresionó desde un pase anterior:",
      unscoredRuns: (n: number) =>
        `${n} corrida${n === 1 ? "" : "s"} terminada${n === 1 ? "" : "s"} todavía no tiene${n === 1 ? "" : "n"} puntaje — la puntuación es a demanda, así que están ausentes de todo resumen de experimento.`,
      openFindings: (n: number, link: ReactNode) => (
        <>
          {n} {link} abierto{n === 1 ? "" : "s"} sin nada que lo{n === 1 ? "" : "s"} pruebe todavía.
        </>
      ),
      findingsLink: (n: number) => `hallazgo${n === 1 ? "" : "s"}`,
      noRegressionGate:
        "Ningún caso trae su propia validación objetiva, así que nada acá puede condicionar un cambio al arnés.",
      nothingOutstanding: "Nada pendiente."
    },
    page: {
      eyebrow: "Benchmarks",
      title: "Calidad del arnés",
      lede: "Si el arnés está mejorando, si algo está regresionando, y cuánto cuesta un éxito. Cada cifra lleva su denominador — una tasa sobre cuatro corridas y otra sobre cuatrocientas son afirmaciones distintas.",
      cases: "Casos",
      runs: "Corridas",
      running: "Corriendo",
      completed: "Completadas"
    },
    leaderboard: {
      heading: "Leaderboard — ranking por condición",
      conditionsRuns: (conditions: number, runs: number) =>
        `${conditions} condición${conditions === 1 ? "" : "es"} · ${runs} corrida${runs === 1 ? "" : "s"}`,
      filterLane: "Filtrar por carril",
      laneAll: "carril: todos",
      filterHarness: "Filtrar por arnés",
      harnessAll: "arnés: todos",
      filterModel: "Filtrar por modelo",
      modelAll: "modelo: todos",
      filterPrompt: "Filtrar por prompt",
      promptAll: "prompt: todos",
      filterVersion: "Filtrar por versión de arnés",
      versionAll: "versión: todas",
      officialTooltip:
        "Filas de referencia del leaderboard oficial (data/reference/swebench_official.json).",
      officialReference: "referencia oficial",
      continuationsTooltip:
        "Corridas continuadas desde otra corrida (ADR-0026). Una persona leyó el workspace y escribió un prompt nuevo, así que no son comparables con un intento de un solo paso.",
      includeContinuations: "incluir continuaciones",
      noRuns: "Todavía no hay corridas terminales — lanzá un caso desde la página de Corridas para poblar el ranking.",
      colRank: "#",
      colCondition: "Condición",
      colPctResolved: "% resuelto",
      colRuns: "Corridas",
      colAvgScore: "Puntaje prom.",
      colBest: "Mejor",
      colAvgCost: "Costo prom.",
      officialBadge: "oficial",
      officialBadgeTooltip:
        "Fila de referencia del leaderboard oficial — subconjunto e infraestructura distintos, comparación indicativa únicamente.",
      source: "fuente ↗",
      officialPctTooltip: "pass@1 oficial sobre las 500 instancias Verified",
      resolvedTooltip: (count: number, total: number) =>
        `${count}/${total} corridas objetivamente validadas resueltas`,
      resolvedCount: (count: number, total: number) => `${count}/${total} resueltas`,
      footerNote: (
        <>
          ⚠️ Comparación indicativa: tu % de corridas es sobre el subconjunto curado del lab y
          tus condiciones; las filas oficiales son pass@1 sobre las 500 instancias SWE-bench
          Verified. Actualizá la referencia editando{" "}
          <code>data/reference/swebench_official.json</code>.
        </>
      )
    }
  }
});

/**
 * A sparkline of weekly success rate.
 *
 * A week with no validated run is a **gap**, not a zero: drawing it on the
 * baseline would show a collapse that never happened.
 */
function Trend({ points }: { points: OverviewReport["trend"] }) {
  const m = useMessages(MESSAGES);
  const measured = points.filter((point) => point.rate !== null);
  if (measured.length < 2) {
    return <p className="muted small">{m.trend.notEnough}</p>;
  }
  const width = 240;
  const height = 44;
  const step = width / Math.max(points.length - 1, 1);
  return (
    <svg
      aria-label={m.trend.ariaLabel}
      height={height}
      role="img"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
    >
      {points.map((point, index) =>
        point.rate === null ? null : (
          <circle
            cx={index * step}
            cy={height - point.rate * height}
            fill="currentColor"
            key={point.week_ending}
            r={2.5}
          >
            <title>{m.trend.tooltip(point.week_ending, pct(point.rate), point.n)}</title>
          </circle>
        )
      )}
      <polyline
        fill="none"
        points={points
          .map((point, index) =>
            point.rate === null
              ? null
              : `${index * step},${height - point.rate * height}`
          )
          .filter(Boolean)
          .join(" ")}
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </svg>
  );
}

/**
 * The score corpus, aggregated.
 *
 * The figures above come from `Validation`. These come from the registry, and
 * they answer a different question: not "did the runs pass?" but "is the
 * harness earning its keep?" — which is the question this lab exists for and
 * which nothing else on this page would ever surface.
 */
function HarnessSignal({ scores }: { scores: OverviewReport["scores"] }) {
  const m = useMessages(MESSAGES);
  if (scores.runs === 0) {
    return (
      <section className="panel">
        <h2>{m.harnessSignal.heading}</h2>
        <p className="muted">{m.harnessSignal.noScores}</p>
      </section>
    );
  }

  const adherence = scores.adherence;
  const useful = scores.useful?.mean ?? null;
  const redundant = scores.redundant?.mean ?? null;
  const forbidden = scores.forbidden;

  return (
    <section className="panel">
      <div className="row space-between">
        <h2>{m.harnessSignal.heading}</h2>
        <span className="muted small">{m.harnessSignal.overRuns(scores.runs)}</span>
      </div>

      <div className="metrics">
        <div className="metric">
          <span>{m.harnessSignal.skillAdherence}</span>
          <strong>{adherence?.mean === null || adherence === null
            ? "—"
            : pct(adherence.mean)}</strong>
          <small className="muted">{m.harnessSignal.shareJudgedUsed}</small>
        </div>
        <div className="metric">
          <span>{m.harnessSignal.itemsUseful}</span>
          <strong>{useful === null ? "—" : useful.toFixed(1)}</strong>
          <small className="muted">
            {redundant === null ? "" : m.harnessSignal.judgedRedundant(redundant.toFixed(1))}
          </small>
        </div>
        <div className="metric">
          <span>{m.harnessSignal.diffSize}</span>
          <strong>
            {scores.diff_size?.median === null || !scores.diff_size
              ? "—"
              : m.harnessSignal.lines(scores.diff_size.median)}
          </strong>
          <small className="muted">{m.harnessSignal.medianObservation}</small>
        </div>
        <div className="metric">
          <span>{m.harnessSignal.boundaryViolations}</span>
          <strong>{forbidden?.mean ? forbidden.maximum : 0}</strong>
          <small className="muted">{m.harnessSignal.forbiddenWorst}</small>
        </div>
      </div>

      {adherence !== null && adherence.mean !== null && adherence.mean < 0.4 && (
        <p className="muted small">{m.harnessSignal.lowAdherenceWarning}</p>
      )}
    </section>
  );
}

function Overview({ report }: { report: OverviewReport }) {
  const m = useMessages(MESSAGES);
  const unscored = report.scoring.terminal - report.scoring.scored;
  return (
    <>
      <div className="metrics">
        <div className="metric">
          <span>{m.overview.harnessVersion}</span>
          <strong>{report.harness_version ?? "—"}</strong>
        </div>
        <div className="metric">
          <span>{m.overview.successRate(report.window_days)}</span>
          <strong>{pct(report.success.rate)}</strong>
          <small className="muted">{m.overview.overValidated(report.success.n)}</small>
        </div>
        <div className="metric">
          <span>{m.overview.regressions}</span>
          <strong>{report.regressions.count}</strong>
          <small className="muted">
            {m.overview.ofCasesHistory(report.regressions.eligible_cases)}
          </small>
        </div>
        <div className="metric">
          <span>{m.overview.medianCost}</span>
          <strong>{usd(report.cost.median_usd)}</strong>
          <small className="muted">
            {m.overview.perSuccess(usd(report.cost.cost_per_success))}
          </small>
        </div>
        <div className="metric">
          <span>{m.overview.regressionSuite}</span>
          <strong>{report.regression_suite.cases}</strong>
          <small className="muted">
            {m.overview.ofActiveCases(report.regression_suite.of_active)}
          </small>
        </div>
        <div className="metric">
          <span>{m.overview.experiments}</span>
          <strong>{report.experiments.recent}</strong>
          <small className="muted">
            {m.overview.thisWindowTotal(report.experiments.total)}
          </small>
        </div>
      </div>

      <div className="row gap wrap">
        <section className="panel">
          <h2>{m.overview.qualityOverTime}</h2>
          <Trend points={report.trend} />
          <p className="muted small">{m.overview.weeklyShareDesc}</p>
        </section>

        <HarnessSignal scores={report.scores} />
      </div>

      <div className="row gap wrap">
        <section className="panel">
          <h2>{m.overview.worthAttention}</h2>
          <ul className="warning-list">
            {report.success.note && <li>⚠ {report.success.note}</li>}
            {report.regressions.count > 0 && (
              <li>
                ⚠ {m.overview.regressedSince}{" "}
                <span className="mono small">
                  {report.regressions.cases.join(", ")}
                </span>
              </li>
            )}
            {unscored > 0 && <li>{m.overview.unscoredRuns(unscored)}</li>}
            {report.findings.open > 0 && (
              <li>
                {m.overview.openFindings(
                  report.findings.open,
                  <Link to="/evidence/findings">
                    {m.overview.findingsLink(report.findings.open)}
                  </Link>
                )}
              </li>
            )}
            {report.regression_suite.cases === 0 && <li>{m.overview.noRegressionGate}</li>}
            {report.success.note === "" &&
              report.regressions.count === 0 &&
              unscored === 0 &&
              report.findings.open === 0 &&
              report.regression_suite.cases > 0 && <li>{m.overview.nothingOutstanding}</li>}
          </ul>
        </section>
      </div>
    </>
  );
}

function countByState(runs: RunRecord[], state: RunRecord["state"]): number {
  return runs.filter((run) => run.state === state).length;
}

function distinct(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort();
}

// One leaderboard entry = one experimental condition (or one official
// reference row). % resolved is per-attempt: resolved runs / objectively
// validated runs of the condition — manual-score rows carry avg score only.
type BoardRow = {
  key: string;
  official: boolean;
  label: string;
  detail: string;
  sourceUrl?: string;
  pct: number | null;
  resolvedCount: number;
  objectiveAttempts: number;
  attempts: number;
  avgScore: number | null;
  bestScore: number | null;
  avgCost: number | null;
};

function buildBoard(rows: LeaderboardRunRow[]): BoardRow[] {
  const groups = new Map<string, LeaderboardRunRow[]>();
  for (const row of rows) {
    const key = [
      row.harness,
      row.executor_model ?? "",
      row.prompt_variant,
      row.harness_version
    ].join("|");
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  return [...groups.entries()].map(([key, members]) => {
    const objective = members.filter((m) => m.resolved !== null);
    const resolvedCount = objective.filter((m) => m.resolved === true).length;
    const scores = members
      .map((m) => m.score)
      .filter((s): s is number => typeof s === "number");
    const costs = members
      .map((m) => m.cost_usd)
      .filter((c): c is number => typeof c === "number");
    const first = members[0];
    return {
      key,
      official: false,
      label: `${first.harness} + ${first.executor_model ?? "?"}`,
      detail: [first.prompt_variant, first.harness_version && `harness ${first.harness_version}`]
        .filter(Boolean)
        .join(" · "),
      pct: objective.length > 0 ? (resolvedCount / objective.length) * 100 : null,
      resolvedCount,
      objectiveAttempts: objective.length,
      attempts: members.length,
      avgScore:
        scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : null,
      bestScore: scores.length > 0 ? Math.max(...scores) : null,
      avgCost: costs.length > 0 ? costs.reduce((a, b) => a + b, 0) / costs.length : null
    };
  });
}

function referenceToBoard(refs: OfficialReferenceRow[]): BoardRow[] {
  return refs.map((ref, index) => ({
    key: `official-${index}`,
    official: true,
    label: `${ref.agent} + ${ref.model}`,
    detail: [ref.dataset && `SWE-bench ${ref.dataset}`, ref.date]
      .filter(Boolean)
      .join(" · "),
    sourceUrl: ref.source_url,
    pct: ref.pct_resolved,
    resolvedCount: 0,
    objectiveAttempts: 0,
    attempts: 0,
    avgScore: null,
    bestScore: null,
    avgCost: null
  }));
}

function fmtPct(pct: number | null): string {
  return pct === null ? "—" : `${pct.toFixed(1)}%`;
}

function fmtNum(value: number | null, digits = 1): string {
  return value === null ? "—" : value.toFixed(digits);
}

export function Dashboard({ cases, runs, apiStatus }: DashboardProps) {
  const m = useMessages(MESSAGES);
  const [rows, setRows] = useState<LeaderboardRunRow[]>([]);
  const [overview, setOverview] = useState<OverviewReport | null>(null);
  const [reference, setReference] = useState<OfficialReferenceRow[]>([]);
  const [laneFilter, setLaneFilter] = useState(ALL);
  const [harnessFilter, setHarnessFilter] = useState(ALL);
  const [modelFilter, setModelFilter] = useState(ALL);
  const [promptFilter, setPromptFilter] = useState(ALL);
  const [versionFilter, setVersionFilter] = useState(ALL);
  const [showOfficial, setShowOfficial] = useState(true);
  // Off by default: a continued run was steered by a human between steps, so
  // ranking it beside a one-shot attempt overstates the harness (ADR-0026).
  const [showContinuations, setShowContinuations] = useState(false);

  useEffect(() => {
    api
    api
      .getOverview()
      .then(setOverview)
      .catch(() => setOverview(null));
    api
      .getLeaderboard()
      .then(setRows)
      .catch(() => setRows([]));
    api
      .getLeaderboardReference()
      .then(setReference)
      .catch(() => setReference([]));
  }, []);

  const filtered = useMemo(
    () =>
      rows.filter(
        (r) =>
          (laneFilter === ALL || r.case_type === laneFilter) &&
          (harnessFilter === ALL || r.harness === harnessFilter) &&
          (modelFilter === ALL || r.executor_model === modelFilter) &&
          (promptFilter === ALL || r.prompt_variant === promptFilter) &&
          (versionFilter === ALL || r.harness_version === versionFilter) &&
          (showContinuations || (r.chain_depth ?? 0) === 0)
      ),
    [
      rows,
      laneFilter,
      harnessFilter,
      modelFilter,
      promptFilter,
      versionFilter,
      showContinuations
    ]
  );

  const board = useMemo(() => {
    const mine = buildBoard(filtered);
    const official = showOfficial ? referenceToBoard(reference) : [];
    return [...mine, ...official].sort((a, b) => {
      if (a.pct === null && b.pct === null) {
        return (b.avgScore ?? -1) - (a.avgScore ?? -1);
      }
      if (a.pct === null) return 1;
      if (b.pct === null) return -1;
      return b.pct - a.pct;
    });
  }, [filtered, reference, showOfficial]);

  const maxPct = Math.max(100, ...board.map((r) => r.pct ?? 0));

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{m.page.eyebrow}</p>
          <h1>{m.page.title}</h1>
          <p className="lede">{m.page.lede}</p>
        </div>
        <div className="status-chip">{apiStatus}</div>
      </header>

      {overview !== null ? (
        <Overview report={overview} />
      ) : (
        <div className="metrics">
          <div className="metric">
            <span>{m.page.cases}</span>
            <strong>{cases.length}</strong>
          </div>
          <div className="metric">
            <span>{m.page.runs}</span>
            <strong>{runs.length}</strong>
          </div>
          <div className="metric">
            <span>{m.page.running}</span>
            <strong>{countByState(runs, "running")}</strong>
          </div>
          <div className="metric">
            <span>{m.page.completed}</span>
            <strong>{countByState(runs, "completed")}</strong>
          </div>
        </div>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>{m.leaderboard.heading}</h2>
          <span className="muted">
            {m.leaderboard.conditionsRuns(
              board.filter((r) => !r.official).length,
              filtered.length
            )}
          </span>
        </div>
        <div className="improvements-toolbar">
          <select
            aria-label={m.leaderboard.filterLane}
            onChange={(e) => setLaneFilter(e.target.value)}
            value={laneFilter}
          >
            <option value={ALL}>{m.leaderboard.laneAll}</option>
            {distinct(rows.map((r) => r.case_type)).map((lane) => (
              <option key={lane} value={lane}>
                {lane}
              </option>
            ))}
          </select>
          <select
            aria-label={m.leaderboard.filterHarness}
            onChange={(e) => setHarnessFilter(e.target.value)}
            value={harnessFilter}
          >
            <option value={ALL}>{m.leaderboard.harnessAll}</option>
            {distinct(rows.map((r) => r.harness)).map((harness) => (
              <option key={harness} value={harness}>
                {harness}
              </option>
            ))}
          </select>
          <select
            aria-label={m.leaderboard.filterModel}
            onChange={(e) => setModelFilter(e.target.value)}
            value={modelFilter}
          >
            <option value={ALL}>{m.leaderboard.modelAll}</option>
            {distinct(rows.map((r) => r.executor_model ?? "")).map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
          <select
            aria-label={m.leaderboard.filterPrompt}
            onChange={(e) => setPromptFilter(e.target.value)}
            value={promptFilter}
          >
            <option value={ALL}>{m.leaderboard.promptAll}</option>
            {distinct(rows.map((r) => r.prompt_variant)).map((prompt) => (
              <option key={prompt} value={prompt}>
                {prompt}
              </option>
            ))}
          </select>
          <select
            aria-label={m.leaderboard.filterVersion}
            onChange={(e) => setVersionFilter(e.target.value)}
            value={versionFilter}
          >
            <option value={ALL}>{m.leaderboard.versionAll}</option>
            {distinct(rows.map((r) => r.harness_version)).map((version) => (
              <option key={version} value={version}>
                {version}
              </option>
            ))}
          </select>
          <label className="switch" title={m.leaderboard.officialTooltip}>
            <input
              checked={showOfficial}
              onChange={(e) => setShowOfficial(e.target.checked)}
              type="checkbox"
            />
            <span>{m.leaderboard.officialReference}</span>
          </label>
          <label className="switch" title={m.leaderboard.continuationsTooltip}>
            <input
              checked={showContinuations}
              onChange={(e) => setShowContinuations(e.target.checked)}
              type="checkbox"
            />
            <span>{m.leaderboard.includeContinuations}</span>
          </label>
        </div>
        {board.length === 0 ? (
          <p className="muted">{m.leaderboard.noRuns}</p>
        ) : (
          <div className="table-scroll">
            <div className="table table--leaderboard">
              <div className="table-row table-head">
                <span>{m.leaderboard.colRank}</span>
                <span>{m.leaderboard.colCondition}</span>
                <span>{m.leaderboard.colPctResolved}</span>
                <span>{m.leaderboard.colRuns}</span>
                <span>{m.leaderboard.colAvgScore}</span>
                <span>{m.leaderboard.colBest}</span>
                <span>{m.leaderboard.colAvgCost}</span>
              </div>
              {board.map((row, index) => (
                <div
                  className={`table-row${row.official ? " leaderboard-official" : ""}`}
                  key={row.key}
                >
                  <span className="muted">{index + 1}</span>
                  <span className="cell-stack">
                    <strong>
                      {row.label}{" "}
                      {row.official ? (
                        <span className="badge neutral" title={m.leaderboard.officialBadgeTooltip}>
                          {m.leaderboard.officialBadge}
                        </span>
                      ) : null}
                    </strong>
                    <small className="muted">
                      {row.detail}
                      {row.official && row.sourceUrl ? (
                        <>
                          {" · "}
                          <a href={row.sourceUrl} rel="noreferrer" target="_blank">
                            {m.leaderboard.source}
                          </a>
                        </>
                      ) : null}
                    </small>
                  </span>
                  <span className="cell-stack">
                    <span className="pct-bar" title={
                      row.official
                        ? m.leaderboard.officialPctTooltip
                        : m.leaderboard.resolvedTooltip(row.resolvedCount, row.objectiveAttempts)
                    }>
                      <span
                        className={`pct-bar-fill${row.official ? " official" : ""}`}
                        style={{ width: `${((row.pct ?? 0) / maxPct) * 100}%` }}
                      />
                      <span className="pct-bar-label">{fmtPct(row.pct)}</span>
                    </span>
                    {!row.official && row.objectiveAttempts > 0 ? (
                      <small className="muted">
                        {m.leaderboard.resolvedCount(row.resolvedCount, row.objectiveAttempts)}
                      </small>
                    ) : null}
                  </span>
                  <span>{row.official ? "—" : row.attempts}</span>
                  <span>{fmtNum(row.avgScore)}</span>
                  <span>{row.bestScore === null ? "—" : row.bestScore}</span>
                  <span>{row.avgCost === null ? "—" : `$${row.avgCost.toFixed(3)}`}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <p className="muted">{m.leaderboard.footerNote}</p>
      </section>
    </section>
  );
}
