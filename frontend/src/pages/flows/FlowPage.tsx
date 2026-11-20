import { Link, Navigate, useLocation, useParams } from "react-router-dom";

import { InfoTooltip } from "../../components/InfoTooltip";
import { defineMessages, useMessages } from "../../i18n";
import { MetricsView } from "./MetricsView";
import { ModelsFlow } from "./ModelsFlow";
import { SkillsHub } from "./SkillsHub";

/**
 * The guided flows, one route (ADR-0093). A strip of the flows sits on top so
 * a person can switch branch without going back to Home; each flow owns its
 * steps and its diagram.
 *
 * Three flows since 2026-11-20: "Models and harness" (compare models, or the
 * harness ladder on one model) and "Skills" (try one, or improve one with the
 * LLM as auditor) each absorbed a sibling that asked the same kind of question.
 * The old routes still answer and land on the right branch.
 */

const FLOWS = ["models", "skills", "metrics"] as const;
type FlowKey = (typeof FLOWS)[number];

/** Former flows → the branch of the flow that absorbed them. */
const MERGED: Record<string, { flow: FlowKey; param: [string, string] }> = {
  harness: { flow: "models", param: ["vary", "levels"] },
  improve: { flow: "skills", param: ["do", "improve"] }
};

const MESSAGES = defineMessages({
  en: {
    eyebrow: "Guided",
    names: {
      models: "Models and harness",
      skills: "Skills",
      metrics: "Metrics"
    } as Record<FlowKey, string>,
    questions: {
      models: "Which model solves this case best — and how much harness does it need?",
      skills: "Does this skill earn a place — and can the LLM make it better?",
      metrics: "What ran, what to improve, and what each number means."
    } as Record<FlowKey, string>,
    tips: {
      models:
        "Two questions, one flow. Compare models: one or two models at the harness level you pick. Harness levels: one model, none → minimal → full, one A/B with a verdict per step up. Every case, 3D included.",
      skills:
        "Try a skill: one clean A/B against minimal, full or both. Improve a skill: an LLM reads the runs that used it and drafts a v2 you review; then v1 vs v2.",
      metrics: "Reads what already ran: compare runs, opportunities to improve, and the criteria behind a verdict."
    } as Record<FlowKey, string>,
    switchTo: "Switch flow"
  },
  es: {
    eyebrow: "Guiado",
    names: {
      models: "Modelos y arnés",
      skills: "Skills",
      metrics: "Métricas"
    } as Record<FlowKey, string>,
    questions: {
      models: "¿Qué modelo resuelve mejor este caso, y cuánto arnés necesita?",
      skills: "¿Esta skill se gana un lugar, y el LLM la puede mejorar?",
      metrics: "Qué corrió, qué mejorar y qué significa cada número."
    } as Record<FlowKey, string>,
    tips: {
      models:
        "Dos preguntas, un flujo. Comparar modelos: uno o dos modelos con el nivel de arnés que elijas. Niveles de arnés: un modelo, sin → minimal → full, un A/B con veredicto por escalón. Todos los casos, también los 3D.",
      skills:
        "Probar una skill: un A/B limpio contra minimal, full o las dos. Mejorar una skill: un LLM lee las corridas que la usaron y escribe una v2 que revisás; después v1 contra v2.",
      metrics: "Lee lo que ya corrió: comparar corridas, oportunidades de mejora y los criterios detrás de un veredicto."
    } as Record<FlowKey, string>,
    switchTo: "Cambiar de flujo"
  }
});

function FlowBody({ flow }: { flow: FlowKey }) {
  switch (flow) {
    case "models":
      return <ModelsFlow />;
    case "skills":
      return <SkillsHub />;
    case "metrics":
      return <MetricsView />;
  }
}

export function FlowPage() {
  const m = useMessages(MESSAGES);
  const { flow = "" } = useParams();
  const location = useLocation();
  const merged = MERGED[flow];
  if (merged) {
    // Keep what the link carried (`?case=`, `?skill=`…) and add the branch.
    const params = new URLSearchParams(location.search);
    params.set(merged.param[0], merged.param[1]);
    return <Navigate replace to={`/flows/${merged.flow}?${params.toString()}`} />;
  }
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
      {/* Keyed by flow (and the models branch) so a switch starts fresh. */}
      <FlowBody flow={key} key={`${key}${location.search.includes("vary=levels") ? "-levels" : ""}`} />
    </section>
  );
}
