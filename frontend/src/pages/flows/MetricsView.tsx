import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../../api";
import { InfoTooltip } from "../../components/InfoTooltip";
import { defineMessages, useMessages } from "../../i18n";
import type {
  AttentionItem,
  ConditionSummary,
  ExperimentRecord,
  ExperimentSummary,
  MetricCriteria,
  SuggestionRecord
} from "../../types";

/**
 * Metrics (ADR-0093): three read-only tabs over what already ran — it creates
 * nothing. Compare the arms of one experiment, see what the corpus says still
 * needs work, and read the definitions behind every number.
 */

type MetricsTab = "compare" | "improve" | "defs";

const TAB_ORDER: MetricsTab[] = ["compare", "improve", "defs"];

/** Outcome metrics first, in this order, then the rest alphabetically. */
const METRIC_ORDER = [
  "task_validation_pass",
  "test_pass_rate",
  "rubric_score",
  "skill_invoked",
  "progress_auc",
  "cost_usd",
  "cost_to_resolve_usd",
  "wall_time_minutes",
  "agent_turns"
];

type ScoreDefinitionRow = {
  name: string;
  direction: string;
  provenance: string;
  category: string;
  description?: string;
  source?: string;
  version?: string;
  minimum?: number | null;
  maximum?: number | null;
};

type ScoreDefinitions = {
  count: number;
  categories: Record<string, ScoreDefinitionRow[]>;
};

const MESSAGES = defineMessages({
  en: {
    title: "Metrics",
    tabs: {
      compare: "Compare runs",
      improve: (n: number) => (n > 0 ? `What to improve (${n})` : "What to improve"),
      defs: "What we measure"
    },
    improveHeading: "What to improve",
    compare: {
      experimentLabel: "Experiment",
      pickExperiment: "Pick an experiment",
      loading: "Loading…",
      noExperiments: "No experiments yet.",
      noSummary: "This experiment has no runs to summarize yet.",
      arm: (label: string, n: number) => `${label} (n=${n})`,
      metric: "Metric",
      compareIndividual: "Compare individual runs",
      howToRead: "How to read it",
      exploratory: (n: number) =>
        `Exploratory: at least one arm has fewer than ${n} scored runs. Read this as a first look, not a result.`,
      judgement: (from: number, to: number) =>
        `A judgement, not a validation: every arm has at least ${from} scored runs, but fewer than ${to}. Enough to form an opinion, not to validate it.`,
      validation: (n: number) => `Validation: every arm has ${n} or more scored runs.`,
      missing: "—"
    },
    improve: {
      groupRegressed: "Regressed: they passed and now fail",
      groupNeverPassed: "Never passed: the case or the harness?",
      groupOther: "Other",
      attentionHeading: "Needs attention",
      noSuggestions: "No suggestions yet.",
      noAttention: "Nothing needs attention right now.",
      tryIt: "Try it →",
      compareModels: "Compare models →"
    },
    defs: {
      count: (n: number) => `${n} metrics`,
      howItsComputed: "How it is computed",
      source: "Source",
      version: "Version",
      range: "Range",
      provenance: { fact: "fact", observation: "observation", judgement: "judgement" },
      direction: { higher_is_better: "↑ better", lower_is_better: "↓ better", neutral: "neutral" },
      criteriaHeading: "Criteria for a verdict",
      repetitionsLabel: "Repetitions",
      repetitions: (below: number, from: number, validationFrom: number) =>
        `Below ${below}: exploratory. From ${from}: a judgement. From ${validationFrom}: a validation.`,
      successIntervalLabel: "Success interval",
      successInterval: (method: string, confidence: number) =>
        `${method}, ${Math.round(confidence * 100)}% confidence.`,
      pruningLabel: "Pruning",
      pruning: (margin: number, relative: number) =>
        `Any observed loss keeps the item. Success margin ${margin} points, relative margin ${relative}%. A ceiling or floor reading is no signal.`,
      where: "where"
    },
    categories: {
      outcome: "Outcome",
      efficiency: "Efficiency",
      trajectory: "Trajectory",
      behavioral: "Behavioral",
      composite: "Composite"
    }
  },
  es: {
    title: "Métricas",
    tabs: {
      compare: "Comparar corridas",
      improve: (n: number) => (n > 0 ? `Qué mejorar (${n})` : "Qué mejorar"),
      defs: "Qué medimos"
    },
    improveHeading: "Qué mejorar",
    compare: {
      experimentLabel: "Experimento",
      pickExperiment: "Elegí un experimento",
      loading: "Cargando…",
      noExperiments: "Todavía no hay experimentos.",
      noSummary: "Este experimento todavía no tiene corridas para resumir.",
      arm: (label: string, n: number) => `${label} (n=${n})`,
      metric: "Métrica",
      compareIndividual: "Comparar corridas individuales",
      howToRead: "Cómo leerlo",
      exploratory: (n: number) =>
        `Exploratorio: al menos un brazo tiene menos de ${n} corridas puntuadas. Leé esto como una primera mirada, no como un resultado.`,
      judgement: (from: number, to: number) =>
        `Un juicio, no una validación: cada brazo tiene al menos ${from} corridas puntuadas, pero menos de ${to}. Alcanza para opinar, no para validar.`,
      validation: (n: number) => `Validación: cada brazo tiene ${n} o más corridas puntuadas.`,
      missing: "—"
    },
    improve: {
      groupRegressed: "Regresó: pasaba y ahora falla",
      groupNeverPassed: "Nunca pasó: ¿el caso o el arnés?",
      groupOther: "Otros",
      attentionHeading: "Necesita atención",
      noSuggestions: "Todavía no hay sugerencias.",
      noAttention: "Por ahora nada necesita atención.",
      tryIt: "Probarla →",
      compareModels: "Comparar modelos →"
    },
    defs: {
      count: (n: number) => `${n} métricas`,
      howItsComputed: "Cómo se calcula",
      source: "Fuente",
      version: "Versión",
      range: "Rango",
      provenance: { fact: "hecho", observation: "observación", judgement: "juicio" },
      direction: { higher_is_better: "↑ mejor", lower_is_better: "↓ mejor", neutral: "neutral" },
      criteriaHeading: "Criterios para un veredicto",
      repetitionsLabel: "Repeticiones",
      repetitions: (below: number, from: number, validationFrom: number) =>
        `Menos de ${below}: exploratorio. Desde ${from}: un juicio. Desde ${validationFrom}: una validación.`,
      successIntervalLabel: "Intervalo de éxito",
      successInterval: (method: string, confidence: number) =>
        `${method}, ${Math.round(confidence * 100)}% de confianza.`,
      pruningLabel: "Poda",
      pruning: (margin: number, relative: number) =>
        `Cualquier pérdida observada mantiene el ítem. Margen de éxito ${margin} puntos, margen relativo ${relative}%. Un techo o piso cuenta como sin señal.`,
      where: "dónde"
    },
    categories: {
      outcome: "Resultado",
      efficiency: "Eficiencia",
      trajectory: "Trayectoria",
      behavioral: "Comportamiento",
      composite: "Compuesto"
    }
  }
});

function formatCell(name: string, value: number | null): string {
  if (value === null || value === undefined) return "—";
  if (name.endsWith("_usd")) return `$${value.toFixed(2)}`;
  if (name === "wall_time_minutes") return `${value.toFixed(1)} min`;
  if (
    name === "task_validation_pass" ||
    name === "test_pass_rate" ||
    name === "skill_invoked" ||
    (value >= 0 && value <= 1 && name !== "agent_turns" && name !== "rubric_score")
  ) {
    return `${Math.round(value * 100)}%`;
  }
  return value.toFixed(2);
}

function orderedMetricNames(conditions: ConditionSummary[]): string[] {
  const present = new Set<string>();
  for (const condition of conditions) {
    for (const name of Object.keys(condition.metrics)) present.add(name);
  }
  const known = METRIC_ORDER.filter((name) => present.has(name));
  const rest = [...present].filter((name) => !METRIC_ORDER.includes(name)).sort();
  return [...known, ...rest];
}

function minArmN(conditions: ConditionSummary[]): number {
  if (conditions.length === 0) return 0;
  return Math.min(...conditions.map((condition) => condition.scored_runs));
}

/** Tab 1: one experiment's arms, side by side. */
function CompareTab({
  m,
  experiments,
  loadingExperiments,
  definitionByName,
  criteria
}: {
  m: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
  experiments: ExperimentRecord[];
  loadingExperiments: boolean;
  definitionByName: Map<string, ScoreDefinitionRow>;
  criteria: MetricCriteria | null;
}) {
  const [params, setParams] = useSearchParams();
  const experimentId = params.get("experiment") ?? "";
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);

  useEffect(() => {
    if (!experimentId) {
      setSummary(null);
      return;
    }
    let active = true;
    setSummaryLoading(true);
    api
      .summarizeExperiment(experimentId)
      .then((result) => active && setSummary(result))
      .catch(() => active && setSummary(null))
      .finally(() => active && setSummaryLoading(false));
    return () => {
      active = false;
    };
  }, [experimentId]);

  const conditions = summary?.conditions ?? [];
  const metricNames = useMemo(() => orderedMetricNames(conditions), [conditions]);
  const minN = minArmN(conditions);

  const readingNote = useMemo(() => {
    if (!criteria || conditions.length === 0) return null;
    const { exploratory_below, judgement_from, validation_from } = criteria.repetitions;
    if (minN < exploratory_below) return m.compare.exploratory(exploratory_below);
    if (minN < validation_from) return m.compare.judgement(judgement_from, validation_from);
    return m.compare.validation(validation_from);
  }, [criteria, conditions.length, minN, m]);

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <h2>{m.compare.experimentLabel}</h2>
        </div>
        <div className="form-grid">
          <label>
            <span>{m.compare.experimentLabel}</span>
            <select
              onChange={(event) => setParams({ tab: "compare", experiment: event.target.value })}
              value={experimentId}
            >
              <option value="">{m.compare.pickExperiment}</option>
              {experiments.map((experiment) => (
                <option key={experiment.experiment_id} value={experiment.experiment_id}>
                  {experiment.experiment_id} — {experiment.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {loadingExperiments ? <p className="muted">{m.compare.loading}</p> : null}
        {!loadingExperiments && experiments.length === 0 ? (
          <p className="muted">{m.compare.noExperiments}</p>
        ) : null}
      </section>

      {experimentId ? (
        <section className="panel">
          <div className="panel-head">
            <h2>{experimentId}</h2>
            <Link to="/advanced/compare">{m.compare.compareIndividual}</Link>
          </div>
          {summaryLoading ? <p className="muted">{m.compare.loading}</p> : null}
          {!summaryLoading && conditions.length === 0 ? (
            <p className="muted">{m.compare.noSummary}</p>
          ) : null}
          {!summaryLoading && conditions.length > 0 ? (
            <>
              <div className="metrics-table-wrap">
                <table className="metrics-table">
                  <thead>
                    <tr>
                      <th>{m.compare.metric}</th>
                      {conditions.map((condition) => (
                        <th key={condition.label}>
                          {m.compare.arm(condition.label, condition.scored_runs)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {metricNames.map((name) => {
                      const definition = definitionByName.get(name);
                      const values = conditions.map(
                        (condition) => condition.metrics[name]?.mean ?? null
                      );
                      let bestIndex = -1;
                      if (definition && definition.direction !== "neutral") {
                        const candidates = values
                          .map((value, index) => ({ value, index }))
                          .filter(({ value }) => value !== null) as { value: number; index: number }[];
                        if (candidates.length > 0) {
                          const pick =
                            definition.direction === "higher_is_better"
                              ? candidates.reduce((a, b) => (b.value > a.value ? b : a))
                              : candidates.reduce((a, b) => (b.value < a.value ? b : a));
                          bestIndex = pick.index;
                        }
                      }
                      return (
                        <tr key={name}>
                          <th scope="row">
                            <code>{name}</code>
                            {definition?.description ? <InfoTooltip text={definition.description} /> : null}
                          </th>
                          {values.map((value, index) => (
                            <td className={index === bestIndex ? "metrics-best" : ""} key={conditions[index].label}>
                              {formatCell(name, value)}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {readingNote ? (
                <div className="callout">
                  <p>
                    <strong>{m.compare.howToRead}</strong>
                  </p>
                  <p className="muted">{readingNote}</p>
                </div>
              ) : null}
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

const SEVERITY_CLASS: Record<string, string> = {
  critical: "metrics-dot metrics-dot--critical",
  warning: "metrics-dot metrics-dot--warning",
  info: "metrics-dot metrics-dot--info"
};

function groupSuggestion(title: string, m: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>): string {
  if (title.toLowerCase().includes("regressed")) return m.improve.groupRegressed;
  if (title.toLowerCase().includes("never passed")) return m.improve.groupNeverPassed;
  return m.improve.groupOther;
}

/** Tab 2: suggestions and attention items, each with a way into it. */
function ImproveTab({
  m,
  suggestions,
  attentionItems,
  caseSkillAvailability
}: {
  m: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
  suggestions: SuggestionRecord[];
  attentionItems: AttentionItem[];
  caseSkillAvailability: Map<string, string | null>;
}) {
  const groups = useMemo(() => {
    const order = [m.improve.groupRegressed, m.improve.groupNeverPassed, m.improve.groupOther];
    const grouped = new Map<string, SuggestionRecord[]>();
    for (const key of order) grouped.set(key, []);
    for (const suggestion of suggestions) {
      const key = groupSuggestion(suggestion.title, m);
      grouped.get(key)?.push(suggestion);
    }
    return order.map((key) => ({ key, rows: grouped.get(key) ?? [] })).filter((group) => group.rows.length > 0);
  }, [suggestions, m]);

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <h2>{m.improveHeading}</h2>
        </div>
        {suggestions.length === 0 ? <p className="muted">{m.improve.noSuggestions}</p> : null}
        {groups.map((group) => (
          <div className="metrics-group" key={group.key}>
            <h3>{group.key}</h3>
            <ul className="metrics-row-list">
              {group.rows.map((suggestion) => {
                const caseId = suggestion.affected_case_ids[0] ?? "";
                const skillName = caseSkillAvailability.get(caseId);
                return (
                  <li className="metrics-row" key={suggestion.key}>
                    <span className={SEVERITY_CLASS[suggestion.severity === "high" ? "critical" : suggestion.severity === "low" ? "info" : "warning"]} />
                    <div className="metrics-row-body">
                      <strong>{suggestion.title}</strong>
                      <p className="muted">{suggestion.recommendation}</p>
                    </div>
                    {skillName ? (
                      <Link
                        className="secondary"
                        to={`/flows/skills?do=try&source=case&skill=${encodeURIComponent(skillName)}&case=${encodeURIComponent(caseId)}`}
                      >
                        {m.improve.tryIt}
                      </Link>
                    ) : caseId ? (
                      <Link className="secondary" to={`/flows/models?case=${encodeURIComponent(caseId)}`}>
                        {m.improve.compareModels}
                      </Link>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.improve.attentionHeading}</h2>
        </div>
        {attentionItems.length === 0 ? <p className="muted">{m.improve.noAttention}</p> : null}
        <ul className="metrics-row-list">
          {attentionItems.map((item, index) => (
            <li className="metrics-row" key={`${item.kind}-${index}`}>
              <span className={SEVERITY_CLASS[item.severity] ?? SEVERITY_CLASS.info} />
              <div className="metrics-row-body">
                <strong>{item.headline}</strong>
                <p className="muted">{item.detail}</p>
              </div>
              <Link className="secondary" to={item.route}>
                {item.action}
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** Tab 3: the registry and the criteria that turn its numbers into a verdict. */
function DefsTab({
  m,
  definitions,
  criteria
}: {
  m: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
  definitions: ScoreDefinitions | null;
  criteria: MetricCriteria | null;
}) {
  const categoryLabel = (category: string) =>
    (m.categories as Record<string, string>)[category] ?? category;
  const provenanceLabel = (provenance: string) =>
    (m.defs.provenance as Record<string, string>)[provenance] ?? provenance;
  const directionLabel = (direction: string) =>
    (m.defs.direction as Record<string, string>)[direction] ?? direction;

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <h2>{m.tabs.defs}</h2>
          {definitions ? <span className="badge neutral">{m.defs.count(definitions.count)}</span> : null}
        </div>
        {definitions &&
          Object.entries(definitions.categories).map(([category, rows]) => (
            <div className="metrics-group" key={category}>
              <h3>{categoryLabel(category)}</h3>
              <ul className="metrics-def-list">
                {rows.map((row) => (
                  <li className="metrics-def-row" key={row.name}>
                    <code>{row.name}</code>
                    <span className="muted">{firstSentence(row.description ?? "")}</span>
                    <span className="hub-chip hub-chip--ui">{provenanceLabel(row.provenance)}</span>
                    <span className="hub-chip hub-chip--cli">{directionLabel(row.direction)}</span>
                    <details className="metrics-def-details">
                      <summary className="muted">{m.defs.howItsComputed}</summary>
                      <dl>
                        <div>
                          <dt>{m.defs.source}</dt>
                          <dd>{row.source || "—"}</dd>
                        </div>
                        <div>
                          <dt>{m.defs.version}</dt>
                          <dd>{row.version || "—"}</dd>
                        </div>
                        {row.minimum !== undefined && row.minimum !== null ? (
                          <div>
                            <dt>{m.defs.range}</dt>
                            <dd>
                              {row.minimum} – {row.maximum ?? "—"}
                            </dd>
                          </div>
                        ) : null}
                        <div>
                          <dt>{m.defs.howItsComputed}</dt>
                          <dd>{row.description}</dd>
                        </div>
                      </dl>
                    </details>
                  </li>
                ))}
              </ul>
            </div>
          ))}
      </section>

      {criteria ? (
        <section className="panel">
          <div className="panel-head">
            <h2>{m.defs.criteriaHeading}</h2>
          </div>
          <ul className="metrics-criteria-list">
            <li>
              <strong>{m.defs.repetitionsLabel}</strong>
              <p className="muted">
                {m.defs.repetitions(
                  criteria.repetitions.exploratory_below,
                  criteria.repetitions.judgement_from,
                  criteria.repetitions.validation_from
                )}
              </p>
              <code>{criteria.repetitions.where}</code>
            </li>
            <li>
              <strong>{m.defs.successIntervalLabel}</strong>
              <p className="muted">
                {m.defs.successInterval(criteria.success_interval.method, criteria.success_interval.confidence)}
              </p>
              <code>{criteria.success_interval.where}</code>
            </li>
            <li>
              <strong>{m.defs.pruningLabel}</strong>
              <p className="muted">
                {m.defs.pruning(
                  Math.round(criteria.pruning.success_margin * 100),
                  Math.round(criteria.pruning.relative_margin * 100)
                )}
              </p>
              <code>{criteria.pruning.where}</code>
            </li>
          </ul>
        </section>
      ) : null}
    </div>
  );
}

const CASE_SKILL_SOURCES = new Set(["pr-review", "security", "containment"]);

/** The first sentence, ending in exactly one period. */
function firstSentence(text: string): string {
  const head = text.split(/(?<=\.)\s/)[0].trim();
  return head.endsWith(".") ? head : `${head}.`;
}

export function MetricsView() {
  const m = useMessages(MESSAGES);
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: MetricsTab = TAB_ORDER.includes(tabParam as MetricsTab) ? (tabParam as MetricsTab) : "compare";

  const [experiments, setExperiments] = useState<ExperimentRecord[]>([]);
  const [experimentsLoading, setExperimentsLoading] = useState(true);
  const [suggestions, setSuggestions] = useState<SuggestionRecord[]>([]);
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [definitions, setDefinitions] = useState<ScoreDefinitions | null>(null);
  const [criteria, setCriteria] = useState<MetricCriteria | null>(null);
  const [caseSkillAvailability, setCaseSkillAvailability] = useState<Map<string, string | null>>(new Map());

  useEffect(() => {
    setExperimentsLoading(true);
    api
      .listExperiments()
      .then(setExperiments)
      .catch(() => setExperiments([]))
      .finally(() => setExperimentsLoading(false));
    api.getScoreDefinitions().then(setDefinitions).catch(() => setDefinitions(null));
    api.getMetricCriteria().then(setCriteria).catch(() => setCriteria(null));
  }, []);

  useEffect(() => {
    api
      .listSuggestions()
      .then(({ suggestions: rows }) => setSuggestions(rows))
      .catch(() => setSuggestions([]));
    api
      .getAttention()
      .then((feed) => setAttention(feed.items))
      .catch(() => setAttention([]));
  }, []);

  // For a suggestion whose case likely ships a candidate skill (pr-review,
  // security, containment), look up whether one is actually available — only
  // for those cases, so this stays a handful of calls, not one per suggestion.
  useEffect(() => {
    let active = true;
    const caseIds = new Set(
      suggestions
        .map((suggestion) => suggestion.affected_case_ids[0])
        .filter(
          (caseId): caseId is string =>
            !!caseId && [...CASE_SKILL_SOURCES].some((source) => caseId.includes(source))
        )
    );
    if (caseIds.size === 0) {
      setCaseSkillAvailability(new Map());
      return;
    }
    Promise.all(
      [...caseIds].map((caseId) =>
        api
          .listCaseCandidateSkills(caseId)
          .then((rows) => [caseId, rows[0]?.name ?? null] as const)
          .catch(() => [caseId, null] as const)
      )
    ).then((pairs) => {
      if (active) setCaseSkillAvailability(new Map(pairs));
    });
    return () => {
      active = false;
    };
  }, [suggestions]);

  const definitionByName = useMemo(() => {
    const map = new Map<string, ScoreDefinitionRow>();
    if (definitions) {
      for (const rows of Object.values(definitions.categories)) {
        for (const row of rows) map.set(row.name, row);
      }
    }
    return map;
  }, [definitions]);

  const setTab = (next: MetricsTab) => {
    const nextParams = new URLSearchParams(params);
    nextParams.set("tab", next);
    setParams(nextParams);
  };

  return (
    <section className="metrics-view">
      <div className="metrics-tabs" role="tablist">
        {TAB_ORDER.map((key) => (
          <button
            aria-selected={tab === key}
            className={tab === key ? "metrics-tab metrics-tab--active" : "metrics-tab"}
            key={key}
            onClick={() => setTab(key)}
            role="tab"
            type="button"
          >
            {key === "improve" ? m.tabs.improve(suggestions.length + attention.length) : m.tabs[key]}
          </button>
        ))}
      </div>

      {tab === "compare" ? (
        <CompareTab
          criteria={criteria}
          definitionByName={definitionByName}
          experiments={experiments}
          loadingExperiments={experimentsLoading}
          m={m}
        />
      ) : null}
      {tab === "improve" ? (
        <ImproveTab
          attentionItems={attention}
          caseSkillAvailability={caseSkillAvailability}
          m={m}
          suggestions={suggestions}
        />
      ) : null}
      {tab === "defs" ? <DefsTab criteria={criteria} definitions={definitions} m={m} /> : null}
    </section>
  );
}
