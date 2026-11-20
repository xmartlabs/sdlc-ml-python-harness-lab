import { Link, Navigate, useParams } from "react-router-dom";

import { InfoTooltip } from "../../components/InfoTooltip";
import { defineMessages, useMessages } from "../../i18n";
import { HarnessFlow } from "./HarnessFlow";
import { ImproveFlow } from "./ImproveFlow";
import { MetricsView } from "./MetricsView";
import { ModelsFlow } from "./ModelsFlow";
import { SkillsFlow } from "./SkillsFlow";

/**
 * The guided flows, one route (ADR-0093). A strip of the flows sits on top so
 * a person can switch branch without going back to Home; each flow owns its
 * steps and its diagram.
 */

const FLOWS = ["models", "harness", "skills", "improve", "metrics"] as const;
type FlowKey = (typeof FLOWS)[number];

const MESSAGES = defineMessages({
  en: {
    eyebrow: "Guided",
    names: {
      models: "Models",
      harness: "Harness",
      skills: "Skills",
      improve: "Improve a skill",
      metrics: "Metrics"
    } as Record<FlowKey, string>,
    questions: {
      models: "Which model solves this case best, at what cost?",
      harness: "How much harness does this model need on this case?",
      skills: "Does this skill earn a place — on minimal, on full?",
      improve: "What does this skill miss, and is a rewrite better?",
      metrics: "What ran, what to improve, and what each number means."
    } as Record<FlowKey, string>,
    tips: {
      models: "Varies the model. Case, prompt, steps and repetitions stay fixed. Same design as Compare models.",
      harness: "Varies the harness level: no harness, minimal (7 skills) or full (26). Works on every case, 3D included.",
      skills: "Varies one skill, against minimal, full or both. Each base is one clean A/B; both are read side by side.",
      improve: "An LLM reads the runs that used the skill and drafts a v2 you review; then v1 vs v2 on the same case.",
      metrics: "Reads what already ran: compare runs, opportunities to improve, and the criteria behind a verdict."
    } as Record<FlowKey, string>,
    switchTo: "Switch flow"
  },
  es: {
    eyebrow: "Guiado",
    names: {
      models: "Modelos",
      harness: "Arnés",
      skills: "Skills",
      improve: "Mejorar una skill",
      metrics: "Métricas"
    } as Record<FlowKey, string>,
    questions: {
      models: "¿Qué modelo resuelve mejor este caso, a qué costo?",
      harness: "¿Cuánto arnés necesita este modelo en este caso?",
      skills: "¿Esta skill se gana un lugar, en minimal o en full?",
      improve: "¿Qué le falta a esta skill, y una reescritura es mejor?",
      metrics: "Qué corrió, qué mejorar y qué significa cada número."
    } as Record<FlowKey, string>,
    tips: {
      models: "Varía el modelo. El caso, el prompt, los pasos y las repeticiones quedan fijos. El mismo diseño que Comparar modelos.",
      harness: "Varía el nivel de arnés: sin arnés, minimal (7 skills) o full (26). Funciona en todos los casos, también los 3D.",
      skills: "Varía una skill, contra minimal, full o las dos. Cada base es un A/B limpio; las dos se leen lado a lado.",
      improve: "Un LLM lee las corridas que usaron la skill y escribe una v2 que revisás; después v1 contra v2 en el mismo caso.",
      metrics: "Lee lo que ya corrió: comparar corridas, oportunidades de mejora y los criterios detrás de un veredicto."
    } as Record<FlowKey, string>,
    switchTo: "Cambiar de flujo"
  }
});

function FlowBody({ flow }: { flow: FlowKey }) {
  switch (flow) {
    case "models":
      return <ModelsFlow />;
    case "harness":
      return <HarnessFlow />;
    case "skills":
      return <SkillsFlow />;
    case "improve":
      return <ImproveFlow />;
    case "metrics":
      return <MetricsView />;
  }
}

export function FlowPage() {
  const m = useMessages(MESSAGES);
  const { flow = "" } = useParams();
  if (!(FLOWS as readonly string[]).includes(flow)) {
    return <Navigate replace to="/" />;
  }
  const key = flow as FlowKey;
  return (
    <section className="page flow-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{m.eyebrow}</p>
          <h1>
            {m.names[key]} <InfoTooltip text={m.tips[key]} />
          </h1>
          <p className="lede">{m.questions[key]}</p>
        </div>
      </header>
      <nav aria-label={m.switchTo} className="flow-switch">
        {FLOWS.map((item) => (
          <Link aria-current={item === key ? "page" : undefined} key={item} to={`/flows/${item}`}>
            {m.names[item]}
          </Link>
        ))}
      </nav>
      {/* Keyed by flow so switching flows starts each one fresh. */}
      <FlowBody flow={key} key={key} />
    </section>
  );
}
