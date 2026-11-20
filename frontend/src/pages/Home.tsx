import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { FlowDiagram } from "../components/HubPage";
import { InfoTooltip } from "../components/InfoTooltip";
import { Showcase } from "../components/Showcase";
import { Spinner } from "../components/Spinner";
import {
  IconAlert,
  IconArrowRight,
  IconCheck,
  IconCompare,
  IconCpu,
  IconFiles,
  IconFlask,
  IconInfo,
  IconLayers,
  IconPuzzle,
  IconRepo,
  IconSearch,
  IconTrendUp
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
  { key: "compare_models", icon: IconCompare, to: "/evaluate/compare-models" },
  { key: "discovery", icon: IconSearch, to: "/evaluate/audit" }
] as const;

/**
 * The doors (ADR-0090, ADR-0093): what a newcomer came to try, each opening a
 * guided flow. Models and harness share one door, and so do trying and
 * improving a skill (2026-11-20): each pair is one question with two branches.
 * The evaluation modes above are still one click away, under "in detail".
 */
const DOORS = [
  { key: "models", icon: IconCpu, to: "/flows/models" },
  { key: "skills", icon: IconPuzzle, to: "/flows/skills" },
  { key: "metrics", icon: IconTrendUp, to: "/flows/metrics" },
  { key: "benchmarks", icon: IconFiles, to: "/benchmarks" },
  { key: "repo", icon: IconRepo, to: "/your-repo" }
] as const;

const SEVERITY_ICON: Record<string, ComponentType<IconProps>> = {
  critical: IconAlert,
  warning: IconAlert,
  info: IconInfo
};

const MESSAGES = defineMessages({
  en: {
    heading: "What do you want to learn?",
    doorsTitle: "What do you want to try today?",
    doorsLede: "Four ways in. The experiment's design — what stays fixed, what varies — is built by each form, not here.",
    doors: {
      models: { title: "Models and harness", body: "Local vs cloud, a new frontier model, or none → minimal → full on one model.", tags: ["LM Studio", "Claude", "3D too", "no harness"], tip: "Compare models: one or two, at the harness level you pick. Harness levels: one model, none → minimal → full, an A/B with a verdict per step up." },
      skills: { title: "Skills", body: "Does this skill earn a place? And can the LLM make it better?", tags: ["the case's", "catalog", "yours", "LLM audit"], tip: "Try a skill: from the case, the catalog, another repo or written by hand, against minimal, full or both. Improve a skill: the LLM reads its runs, drafts a v2 you review, and sets up v1 vs v2." },
      metrics: { title: "Metrics", body: "Compare runs, what to improve, what each number means.", tags: ["compare", "criteria"], tip: "Reads what already ran. Creates nothing." },
      benchmarks: { title: "Benchmarks", body: "Pick a test case, by family.", tags: ["SWE-bench", "3D games", "Security", "Web"], tip: "Every case carries a check the agent did not write." },
      repo: { title: "Your repo", body: "Check your project's harness and take home what helps.", tags: ["30 seconds", "audit", "export"], tip: "No comparison: it reviews a project of yours and says what it lacks." }
    },
    open: "Open",
    loopTitle: "The lab's loop",
    loopNote: "the same for every door",
    loop: [
      { title: "Pick a case", detail: "Benchmarks" },
      { title: "Run two configurations", detail: "model, harness or skill" },
      { title: "Read the verdict", detail: "Results, with its interval" },
      { title: "Keep what worked", detail: "make harness-export" }
    ],
    routesTitle: "A first journey, by what you have",
    routesNote: "15 to 45 minutes each",
    routes: [
      { who: "A local model", hint: "LM Studio or Ollama", steps: ["Tune it in 5 steps", "Run clinic-slots-t1", "Compare it with a cloud model"], to: "/local/tune" },
      { who: "A new skill", hint: "yours or from GitHub", steps: ["Import it in Skills", "Minimal vs minimal + skill", "Does it earn its place?"], to: "/evidence/skills?tab=import" },
      { who: "A new frontier model", hint: "the one out this week", steps: ["Compare models on a 3D case", "See which phases it passed", "Play what it built"], to: "/evaluate/compare-models" },
      { who: "Your team's repo", hint: "cloned on your machine", steps: ["make harness-check", "Audit it in the UI", "Take home what it lacks"], to: "/evaluate/audit" }
    ],
    modesTitle: "Evaluation modes, in detail",
    modesHint: "Five designs the lab enforces — what each holds fixed and what it varies.",
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
      compare_models: {
        title: "Compare models on one case",
        question:
          "A new frontier model is out. How does it do on the case the last one did — chess, racing, the open city?",
        cta: "Start a model comparison",
        holds: ["The case", "Prompt, steps and repetitions"],
        varies: "The model, each with its harness",
        note: "Reports, per model, the checks each run passed, phase by phase, with cost and time. Every comparison on a case is kept in one family, so the next model is read next to the last."
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
    doorsTitle: "¿Qué querés probar hoy?",
    doorsLede: "Cuatro puertas. El diseño del experimento (qué queda fijo, qué varía) lo arma cada formulario, no esta página.",
    doors: {
      models: { title: "Modelos y arnés", body: "Local contra nube, un frontera nuevo, o sin → minimal → full con un modelo.", tags: ["LM Studio", "Claude", "también 3D", "sin arnés"], tip: "Comparar modelos: uno o dos, con el nivel de arnés que elijas. Niveles de arnés: un modelo, sin → minimal → full, un A/B con veredicto por escalón." },
      skills: { title: "Skills", body: "¿Esta skill se gana un lugar? ¿Y el LLM la puede mejorar?", tags: ["la del caso", "catálogo", "tuya", "auditoría LLM"], tip: "Probar una skill: del caso, del catálogo, de otro repo o escrita a mano, contra minimal, full o las dos. Mejorar una skill: el LLM lee sus corridas, escribe una v2 que revisás y arma v1 contra v2." },
      metrics: { title: "Métricas", body: "Comparar corridas, qué mejorar, qué significa cada número.", tags: ["comparar", "criterios"], tip: "Lee lo que ya corrió. No crea nada." },
      benchmarks: { title: "Benchmarks", body: "Elegí un caso de prueba por familia.", tags: ["SWE-bench", "Juegos 3D", "Seguridad", "Web"], tip: "Cada caso trae un chequeo que el agente no escribió." },
      repo: { title: "Tu repo", body: "Revisá el arnés de tu proyecto y llevate lo que sirve.", tags: ["30 segundos", "auditoría", "export"], tip: "No compara nada: revisa un proyecto tuyo y dice qué le falta." }
    },
    open: "Abrir",
    loopTitle: "El ciclo del lab",
    loopNote: "siempre el mismo, para cualquier puerta",
    loop: [
      { title: "Elegí un caso", detail: "Benchmarks" },
      { title: "Corré dos configuraciones", detail: "modelo, arnés o skill" },
      { title: "Leé el veredicto", detail: "Resultados, con intervalo" },
      { title: "Quedate con lo que sirvió", detail: "make harness-export" }
    ],
    routesTitle: "Primer recorrido, según lo que tengas",
    routesNote: "15 a 45 minutos cada uno",
    routes: [
      { who: "Un modelo local", hint: "LM Studio u Ollama", steps: ["Afinalo en 5 pasos", "Corré clinic-slots-t1", "Comparalo con uno de la nube"], to: "/local/tune" },
      { who: "Una skill nueva", hint: "tuya o de GitHub", steps: ["Importala en Skills", "Minimal vs minimal + skill", "¿Se gana su lugar?"], to: "/evidence/skills?tab=import" },
      { who: "Un modelo frontera nuevo", hint: "el que salió esta semana", steps: ["Compará modelos en un caso 3D", "Mirá qué fases pasó", "Jugá lo que construyó"], to: "/evaluate/compare-models" },
      { who: "El repo de tu equipo", hint: "clonado en tu máquina", steps: ["make harness-check", "Auditalo en la UI", "Llevate lo que le falta"], to: "/evaluate/audit" }
    ],
    modesTitle: "Los modos de evaluación, en detalle",
    modesHint: "Cinco diseños que el lab hace cumplir: qué deja fijo cada uno y qué varía.",
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
      compare_models: {
        title: "Comparar modelos en un caso",
        question:
          "Salió un modelo frontera nuevo. ¿Cómo le va en el caso donde corrió el anterior: ajedrez, carreras, la ciudad abierta?",
        cta: "Empezar una comparación de modelos",
        holds: ["El caso", "Prompt, pasos y repeticiones"],
        varies: "El modelo, cada uno con su harness",
        note: "Reporta, por modelo, los chequeos que pasó cada corrida, fase por fase, con costo y tiempo. Todas las comparaciones de un caso quedan en una familia, así el próximo modelo se lee al lado del anterior."
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

  return (
    <section className="page home">
      <header className="page-header">
        <div>
          <h1>{m.doorsTitle}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      {error ? <ErrorBanner message={error === UNABLE_TO_REACH ? m.unableToReach : error} /> : null}

      <Showcase />

      <div className="home-doors">
        {DOORS.map((door) => {
          const Icon = door.icon;
          const text = m.doors[door.key];
          return (
            <div className="home-door" key={door.key}>
              <span className="home-door-icon">
                <Icon size={20} />
              </span>
              <h2>
                {/* The card's link covers the card; the ⓘ sits above it, so a
                    link never holds a button. */}
                <Link className="home-door-link" to={door.to}>
                  {text.title}
                </Link>
                <span className="home-door-info">
                  <InfoTooltip text={text.tip} />
                </span>
              </h2>
              <p className="muted">{text.body}</p>
              <span className="home-door-tags">
                {text.tags.map((tag) => (
                  <span key={tag}>{tag}</span>
                ))}
              </span>
              <span className="home-door-go">
                {m.open}
                <IconArrowRight size={14} />
              </span>
            </div>
          );
        })}
      </div>

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

      <section className="panel home-loop">
        <div className="panel-head">
          <h2>{m.loopTitle}</h2>
          <span className="muted">{m.loopNote}</span>
        </div>
        <FlowDiagram steps={m.loop} />
      </section>

      <section className="panel home-routes">
        <div className="panel-head">
          <h2>{m.routesTitle}</h2>
          <span className="muted">{m.routesNote}</span>
        </div>
        <div className="home-route-list">
          {m.routes.map((route) => (
            <Link className="home-route" key={route.who} to={route.to}>
              <span className="home-route-who">
                <strong>{route.who}</strong>
                <small className="muted">{route.hint}</small>
              </span>
              <ol className="home-route-steps">
                {route.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </Link>
          ))}
        </div>
      </section>

      <details className="panel home-modes">
        <summary>
          <strong>{m.modesTitle}</strong>
          <span className="muted">{m.modesHint}</span>
        </summary>
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
      </details>

      <section className="panel subtle honesty">
        <h2>{m.honestyTitle}</h2>
        <ul className="onboarding-steps">
          {m.honesty.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      {/* Shown always (ADR-0090): a newcomer joining an established lab is
          still new, and the first validated run said nothing about them. */}
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
