import { useSearchParams } from "react-router-dom";

import { InfoTooltip } from "../../components/InfoTooltip";
import { defineMessages, useMessages } from "../../i18n";
import { ImproveFlow } from "./ImproveFlow";
import { SkillsFlow } from "./SkillsFlow";

/**
 * One door for skills (ADR-0093, amended 2026-11-20): try a skill, or improve
 * one with the LLM as auditor. The two were separate cards and menu entries,
 * but they are one loop — try it, read why it failed, draft a v2, try v1 vs v2
 * — so they share a page and the choice is the first thing on it.
 *
 * The choice lives in `?do=`, so a link can open either branch: Metrics sends
 * "try the case's skill" to `?do=try&...`, and `/flows/improve` lands on
 * `?do=improve`. Each branch keeps its own steps and diagram.
 */

type Branch = "try" | "improve";

const MESSAGES = defineMessages({
  en: {
    label: "What do you want to do with a skill?",
    tryTitle: "Try a skill",
    trySub: "does it earn a place, on minimal or full?",
    tryTip:
      "A clean A/B: the same case and model with and without the skill. The skill can be the case's own, one from the catalog, imported from another repo or written by hand.",
    improveTitle: "Improve a skill",
    improveSub: "the LLM as auditor: what it misses, a v2, v1 vs v2",
    improveTip:
      "An LLM reads the runs that used the skill and drafts a v2 you review and edit. Then v1 vs v2 on the same case and model."
  },
  es: {
    label: "¿Qué querés hacer con una skill?",
    tryTitle: "Probar una skill",
    trySub: "¿se gana un lugar, en minimal o en full?",
    tryTip:
      "Un A/B limpio: el mismo caso y modelo con y sin la skill. La skill puede ser la del caso, una del catálogo, importada de otro repo o escrita a mano.",
    improveTitle: "Mejorar una skill",
    improveSub: "el LLM como auditor: qué le falta, una v2, v1 contra v2",
    improveTip:
      "Un LLM lee las corridas que usaron la skill y escribe una v2 que revisás y editás. Después v1 contra v2 en el mismo caso y modelo."
  }
});

export function SkillsHub() {
  const m = useMessages(MESSAGES);
  const [params, setParams] = useSearchParams();
  const branch: Branch = params.get("do") === "improve" ? "improve" : "try";

  const choose = (next: Branch) => {
    if (next === branch) return;
    // A fresh branch: the other one's answers (skill, case…) do not carry over.
    setParams(new URLSearchParams({ do: next }));
  };

  const cards: { key: Branch; title: string; sub: string; tip: string }[] = [
    { key: "try", title: m.tryTitle, sub: m.trySub, tip: m.tryTip },
    { key: "improve", title: m.improveTitle, sub: m.improveSub, tip: m.improveTip }
  ];

  return (
    <>
      <div aria-label={m.label} className="guide-options skills-branch" role="group">
        {cards.map((card) => (
          <div className="guide-option" data-pressed={branch === card.key} key={card.key}>
            <button
              aria-pressed={branch === card.key}
              className="guide-option-hit"
              onClick={() => choose(card.key)}
              type="button"
            >
              <strong>{card.title}</strong>
              <span className="muted">{card.sub}</span>
            </button>
            <span className="guide-option-info">
              <InfoTooltip text={card.tip} />
            </span>
          </div>
        ))}
      </div>
      {/* Keyed so switching branch starts the other one fresh. */}
      {branch === "improve" ? <ImproveFlow key="improve" /> : <SkillsFlow key="try" />}
    </>
  );
}
