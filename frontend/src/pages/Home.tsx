import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import {
  IconAlert,
  IconArrowRight,
  IconCheck,
  IconFlask,
  IconInfo,
  IconLayers,
  IconPuzzle,
  IconSearch
} from "../components/icons";
import type { IconProps } from "../components/icons";
import { defineMessages, useMessages } from "../i18n";
import type { AttentionFeed, AttentionItem, EvidenceSummary } from "../types";
import type { ComponentType } from "react";

/**
 * The three questions this lab can answer, as the way in.
 *
 * The old home page opened with a leaderboard, which answered "what data exists
 * in the system?" — a question nobody arrives with. These are the questions
 * people actually arrive with, and each one maps to an evaluation mode whose
 * design constraints are enforced downstream (ADR-0028).
 *
 * Subject validation leads, and the order is not a matter of taste: the other
 * two doors cannot be opened on a fresh instance (an audit needs a registered
 * repository, a cross-repo comparison needs two), while this one needs nothing
 * and *produces* what they consume — a staged skill and a case that declares
 * what success is. `cross_repo` keeps its mode and its card; the card now lives
 * on Repositories, where the repositories it needs are registered.
 *
 * Structure only (icon, route). Each mode's text lives in `MESSAGES.modes`,
 * keyed the same way, so it is read at render time in the active language.
 */
const MODES = [
  { key: "subject_validation", icon: IconPuzzle, to: "/evaluate/subject" },
  { key: "harness_evaluation", icon: IconFlask, to: "/evaluate/new?mode=harness_evaluation" },
  { key: "harness_by_model", icon: IconLayers, to: "/evaluate/harness-by-model" },
  { key: "discovery", icon: IconSearch, to: "/evaluate/audit" }
] as const;

const SEVERITY_ICON: Record<string, ComponentType<IconProps>> = {
  critical: IconAlert,
  warning: IconAlert,
  info: IconInfo
};

const MESSAGES = defineMessages({
  en: {
    heading: "What do you want to learn?",
    lede: "An evaluation instrument for AI-assisted software engineering. Start from a question; the lab holds you to a design that can answer it.",
    quickStart: {
      title: "Start in 30 seconds — check a repository's harness",
      description:
        "Eight checks decided by code, not by a model: can your verify target fail, can any hook block, how long is the rules file every session loads, are your skills discoverable. No stack, no cost, one fix per finding.",
      openDemoGuide: "Open the demo guide",
      fullAudit: "Full audit in the UI"
    },
    modes: {
      subject_validation: {
        title: "Validate your tool",
        question:
          "I built an agent, an MCP server or a service wrapper, and a skill that drives it. Does it work reliably?",
        cta: "Start tool validation",
        holds: ["The subject", "Cases", "Where it runs"],
        varies: "Harness and model",
        note: "Nothing is ablated, so this says nothing about the harness — it reports one pass rate per harness/model arm, never a pooled one. The only mode that needs no repository registered first."
      },
      harness_evaluation: {
        title: "Evaluate a harness change",
        question:
          "Does this skill, rule, workflow, prompt or harness change actually improve performance?",
        cta: "Start harness evaluation",
        holds: ["Where it runs", "Cases", "Model"],
        varies: "Harness condition",
        note: "Works on this lab's own harness — the continuous-improvement loop — or on an external repository. The only mode that can support a causal claim, and only with enough repetitions per arm."
      },
      harness_by_model: {
        title: "Does a stronger model need less harness?",
        question:
          "Run the same cases with and without the harness on several models, and see whether the harness effect shrinks as the model grows.",
        cta: "Start harness x model",
        holds: ["Cases", "Where it runs"],
        varies: "Harness level and model, as a full grid",
        note: "Reports the harness effect per model and how it changes from the first model to the last, with an interval. Below three cases the change is never called established."
      },
      discovery: {
        title: "Audit a repository",
        question: "What can we learn from how this project or team currently works with AI?",
        cta: "Start repository audit",
        holds: [] as string[],
        varies: "Nothing — this is exploratory",
        note: "Registers the repository through your own clone, pins a commit, reads what it already ships, and ends when one case passes. Findings and the report accumulate on the repository's workbench. It never produces a causal verdict."
      }
    },
    heldFixed: "Held fixed",
    varies: "Varies",
    honestyTitle: "What the lab will — and will not — tell you",
    honesty: [
      "It measures a harness and a workflow — rules, skills, hooks, verification — never a person. No result is per developer.",
      "Repetitions decide how far a result goes: one is exploratory, two to four a judgement, five or more a validation — and every rate carries its interval so you can see how wide it still is.",
      "A result holds for the cases, model and harness version it ran on. Every run records all three."
    ],
    onboardingTitle: "New to the lab? Six journeys, one page each",
    onboarding: [
      { doc: "create-case", text: "Create a case — from an idea with AI, a SWE-bench issue, or your repository." },
      { doc: "run", text: "Run and compare — models, harnesses and skills, repeated; continue a run that stopped short." },
      { doc: "read-results", text: "Read the results — the validity banner first, then the verdict and its interval." },
      { doc: "bring-your-own", text: "Bring your repo or skill — audit its harness, measure the skill." },
      { doc: "take-it-home", text: "Take it home — copy the harness, or one skill, into your project." },
      { doc: "improve-template", text: "Improve the template — turn evidence into a better ml-python-base." }
    ],
    attentionTitle: "What needs attention?",
    readingCorpus: "Reading the corpus",
    attentionClear:
      "Nothing is waiting on you. Every experiment has enough repetitions, every finished run is scored, and no case has regressed.",
    whatLabHolds: "What the lab holds",
    openBenchmarks: "Open Benchmarks",
    activeExperiments: "Active experiments",
    repositories: "Repositories",
    cases: "Cases",
    validatedRunsPassing: (days: number) => `Validated runs passing (${days}d)`,
    noValidatedRuns: "No validated runs yet",
    validatedRunsNote: (n: number) =>
      `over ${n} validated run(s), pooled across cases, models and harnesses — activity, not an experiment result`,
    medianRunCost: "Median run cost",
    openFindings: "Open findings",
    unableToReach: "Unable to reach the lab"
  },
  es: {
    heading: "¿Qué querés aprender?",
    lede: "Un instrumento de evaluación para ingeniería de software asistida por IA. Arrancá de una pregunta; el lab te exige un diseño que pueda responderla.",
    quickStart: {
      title: "Empezá en 30 segundos — revisá el arnés de un repositorio",
      description:
        "Ocho chequeos que decide el código, no un modelo: si tu target de verificación puede fallar, si algún hook puede bloquear, cuánto mide el archivo de reglas que carga cada sesión, si tus skills son descubribles. Sin stack, sin costo, una corrección por hallazgo.",
      openDemoGuide: "Abrí la guía de la demo",
      fullAudit: "Auditoría completa en la UI"
    },
    modes: {
      subject_validation: {
        title: "Validá tu herramienta",
        question:
          "Construí un agente, un servidor MCP o un wrapper de servicio, y una skill que lo maneja. ¿Funciona de forma confiable?",
        cta: "Empezar validación de herramienta",
        holds: ["El sujeto", "Casos", "Dónde corre"],
        varies: "Arnés y modelo",
        note: "Nada se ablaciona, así que esto no dice nada sobre el arnés — reporta una tasa de éxito por brazo de arnés/modelo, nunca una combinada. El único modo que no necesita un repositorio registrado antes."
      },
      harness_evaluation: {
        title: "Evaluar un cambio de arnés",
        question:
          "¿Esta skill, regla, workflow, prompt o cambio de arnés realmente mejora el rendimiento?",
        cta: "Empezar evaluación de arnés",
        holds: ["Dónde corre", "Casos", "Modelo"],
        varies: "Condición de arnés",
        note: "Funciona sobre el propio arnés de este lab — el ciclo de mejora continua — o sobre un repositorio externo. El único modo que puede sostener una afirmación causal, y solo con suficientes repeticiones por brazo."
      },
      harness_by_model: {
        title: "¿Un modelo más fuerte necesita menos arnés?",
        question:
          "Corré los mismos casos con y sin el arnés en varios modelos, y mirá si el efecto del arnés se achica a medida que el modelo crece.",
        cta: "Empezar arnés x modelo",
        holds: ["Casos", "Dónde corre"],
        varies: "Nivel de arnés y modelo, como grilla completa",
        note: "Reporta el efecto del arnés por modelo y cómo cambia del primer modelo al último, con un intervalo. Por debajo de tres casos, el cambio nunca se llama establecido."
      },
      discovery: {
        title: "Auditar un repositorio",
        question: "¿Qué podemos aprender de cómo este proyecto o equipo trabaja hoy con IA?",
        cta: "Empezar auditoría de repositorio",
        holds: [] as string[],
        varies: "Nada — esto es exploratorio",
        note: "Registra el repositorio a través de tu propio clon, fija un commit, lee lo que ya trae, y termina cuando un caso pasa. Los hallazgos y el reporte se acumulan en el workbench del repositorio. Nunca produce un veredicto causal."
      }
    },
    heldFixed: "Se mantiene fijo",
    varies: "Varía",
    honestyTitle: "Qué el lab te va a decir — y qué no",
    honesty: [
      "Mide un arnés y un workflow — reglas, skills, hooks, verificación — nunca a una persona. Ningún resultado es por desarrollador.",
      "Las repeticiones deciden hasta dónde llega un resultado: una es exploratoria, de dos a cuatro un juicio, cinco o más una validación — y cada tasa lleva su intervalo para que veas cuán ancho sigue siendo.",
      "Un resultado vale para los casos, el modelo y la versión de arnés con la que corrió. Cada corrida registra los tres."
    ],
    onboardingTitle: "¿Sos nuevo en el lab? Seis recorridos, una página cada uno",
    onboarding: [
      { doc: "create-case", text: "Creá un caso — desde una idea con IA, un issue de SWE-bench o tu repositorio." },
      { doc: "run", text: "Corré y compará — modelos, arneses y skills, con repeticiones; continuá una corrida que se quedó corta." },
      { doc: "read-results", text: "Leé los resultados — primero el aviso de validez, después el veredicto y su intervalo." },
      { doc: "bring-your-own", text: "Traé tu repo o tu skill — auditá su arnés, medí la skill." },
      { doc: "take-it-home", text: "Llevátelo — copiá el arnés, o una sola skill, a tu proyecto." },
      { doc: "improve-template", text: "Mejorá el template — convertí evidencia en un ml-python-base mejor." }
    ],
    attentionTitle: "¿Qué necesita atención?",
    readingCorpus: "Leyendo el corpus",
    attentionClear:
      "No hay nada esperándote. Todo experimento tiene suficientes repeticiones, toda corrida terminada está puntuada, y ningún caso retrocedió.",
    whatLabHolds: "Lo que el lab tiene",
    openBenchmarks: "Abrir Benchmarks",
    activeExperiments: "Experimentos activos",
    repositories: "Repositorios",
    cases: "Casos",
    validatedRunsPassing: (days: number) => `Corridas validadas que pasan (${days}d)`,
    noValidatedRuns: "Todavía no hay corridas validadas",
    validatedRunsNote: (n: number) =>
      `sobre ${n} corrida(s) validada(s), combinadas entre casos, modelos y arneses — actividad, no un resultado de experimento`,
    medianRunCost: "Costo mediano por corrida",
    openFindings: "Hallazgos abiertos",
    unableToReach: "No se pudo conectar con el lab"
  }
});

// Both guard against `undefined`, not just `null`. A number this page cannot
// find must render as an em dash — a home page that throws because one metric
// is missing takes the whole app down with it.
function formatPercent(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

function formatMoney(value: number | null | undefined): string {
  return typeof value === "number" ? `$${value.toFixed(2)}` : "—";
}

// Sentinel for the load-failure fallback: mapped to text at render time so a
// language switch after a failed load still shows the right string. A real
// `Error.message` from the API is shown as is, in whatever language it arrived.
const UNABLE_TO_REACH = "__unable_to_reach__";

function AttentionRow({ item }: { item: AttentionItem }) {
  const Icon = SEVERITY_ICON[item.severity] ?? IconInfo;
  return (
    <li className={`attention-item attention-item--${item.severity}`}>
      <span className="attention-item-icon">
        <Icon size={18} />
      </span>
      <div className="attention-item-body">
        <strong>{item.headline}</strong>
        <p className="muted">{item.detail}</p>
      </div>
      <Link className="secondary attention-item-action" to={item.route}>
        {item.action}
        <IconArrowRight size={14} />
      </Link>
    </li>
  );
}

export function Home() {
  const [attention, setAttention] = useState<AttentionFeed | null>(null);
  const [summary, setSummary] = useState<EvidenceSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const m = useMessages(MESSAGES);

  useEffect(() => {
    let active = true;
    Promise.all([api.getAttention(), api.getEvidenceSummary()])
      .then(([feed, evidence]) => {
        if (!active) {
          return;
        }
        setAttention(feed);
        setSummary(evidence);
        setError(null);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : UNABLE_TO_REACH);
        }
      })
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  // A lab nobody has run anything on yet gets the journeys. Cases cannot be the
  // signal: the benchmark cases are seeded on startup, so a fresh install always
  // has some and the panel never showed.
  const isNewLab = summary !== null && summary.success.n === 0;

  return (
    <section className="page home">
      <header className="page-header">
        <div>
          <h1>{m.heading}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      {error ? <ErrorBanner message={error === UNABLE_TO_REACH ? m.unableToReach : error} /> : null}

      <section className="panel quick-start">
        <div className="quick-start-body">
          <h2>{m.quickStart.title}</h2>
          <p className="muted">{m.quickStart.description}</p>
          <pre className="quick-start-command">
            <code>make harness-check REPO=../your-project</code>
          </pre>
        </div>
        <div className="quick-start-actions">
          <Link className="primary" to="/system/guide?doc=demo">
            {m.quickStart.openDemoGuide}
            <IconArrowRight size={15} />
          </Link>
          <Link className="secondary" to="/evaluate/audit">
            {m.quickStart.fullAudit}
          </Link>
        </div>
      </section>

      <div className="mode-cards">
        {MODES.map((mode) => {
          const Icon = mode.icon;
          const text = m.modes[mode.key];
          return (
            <article className="mode-card" key={mode.key}>
              <span className="mode-card-icon">
                <Icon size={22} />
              </span>
              <h2>{text.title}</h2>
              <p className="mode-card-question">{text.question}</p>
              <dl className="mode-card-design">
                {text.holds.length > 0 ? (
                  <div>
                    <dt>{m.heldFixed}</dt>
                    <dd>{text.holds.join(" · ")}</dd>
                  </div>
                ) : null}
                <div>
                  <dt>{m.varies}</dt>
                  <dd>{text.varies}</dd>
                </div>
              </dl>
              <p className="mode-card-note muted">{text.note}</p>
              <Link className="primary mode-card-cta" to={mode.to}>
                {text.cta}
                <IconArrowRight size={15} />
              </Link>
            </article>
          );
        })}
      </div>

      <section className="panel subtle honesty">
        <h2>{m.honestyTitle}</h2>
        <ul className="onboarding-steps">
          {m.honesty.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      {isNewLab ? (
        <section className="panel subtle onboarding">
          <h2>{m.onboardingTitle}</h2>
          <ol className="onboarding-steps">
            {m.onboarding.map((step) => (
              <li key={step.doc}>
                <Link to={`/system/guide?doc=${step.doc}`}>{step.text}</Link>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      <section className="panel attention">
        <div className="panel-head">
          <h2>{m.attentionTitle}</h2>
        </div>
        {isLoading ? <Spinner label={m.readingCorpus} /> : null}
        {!isLoading && attention?.clear ? (
          <p className="attention-clear">
            <IconCheck size={16} />
            {m.attentionClear}
          </p>
        ) : null}
        {attention && attention.items.length > 0 ? (
          <ul className="attention-list">
            {attention.items.map((item) => (
              <AttentionRow item={item} key={item.kind} />
            ))}
          </ul>
        ) : null}
      </section>

      {summary ? (
        <section className="panel subtle evidence-summary">
          <div className="panel-head">
            <h2>{m.whatLabHolds}</h2>
            <Link className="button-link" to="/advanced/benchmarks">
              {m.openBenchmarks}
            </Link>
          </div>
          <div className="metrics">
            <div className="metric">
              <span>{m.activeExperiments}</span>
              <strong>{summary.experiments_active}</strong>
            </div>
            <div className="metric">
              <span>{m.repositories}</span>
              <strong>{summary.repositories}</strong>
            </div>
            <div className="metric">
              <span>{m.cases}</span>
              <strong>{summary.cases}</strong>
            </div>
            {/*
              Named for what it pools, not for what it sounds like. This mixes
              every case, model, harness and arm in the window — the composite
              the lab refuses everywhere else. Calling it a success rate on the
              front page invited it to be read as a result; calling it what it
              is leaves it useful as a sign of life and nothing more.
            */}
            <div className="metric">
              <span>{m.validatedRunsPassing(summary.window_days)}</span>
              <strong>{formatPercent(summary.success.rate)}</strong>
              <small className="muted">
                {summary.success.n === 0
                  ? m.noValidatedRuns
                  : m.validatedRunsNote(summary.success.n)}
              </small>
              {summary.success.note ? (
                <small className="muted">{summary.success.note}</small>
              ) : null}
            </div>
            <div className="metric">
              <span>{m.medianRunCost}</span>
              <strong>{formatMoney(summary.cost.median_usd)}</strong>
            </div>
            <div className="metric">
              <span>{m.openFindings}</span>
              <strong>{summary.findings_open}</strong>
            </div>
          </div>
        </section>
      ) : null}
    </section>
  );
}
