import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api";
import { HubPage } from "../../components/HubPage";
import type { Capability, FlowStep } from "../../components/HubPage";
import { IconLayers } from "../../components/icons";
import { defineMessages, useMessages } from "../../i18n";
import type { PruningOverviewRow, SkillProfileRecord } from "../../types";

const MESSAGES = defineMessages<{
  title: string;
  lede: string;
  flow: FlowStep[];
  items: Capability[];
  levels: string;
  levelsSource: string;
  skillsCount: (n: number) => string;
  hiddenInMinimal: (n: number) => string;
  lastPrune: string;
  lastPruneMeta: (model: string, cases: number, runs: number) => string;
  decision: Record<"removable" | "keep" | "inconclusive" | "no_signal", string>;
  interval: (lo: string, hi: string) => string;
  openImprovements: string;
  footnote: string;
}>({
  en: {
    title: "Harness & skills",
    lede: "Two harness levels and one question per skill: does it improve the result, or does it only take context?",
    flow: [
      { title: "Pick a skill", detail: "from the catalog or an import" },
      { title: "Minimal vs minimal + skill", detail: "the skill is asked for" },
      { title: "Read the verdict", detail: "target cases and one general case" },
      { title: "Promote it by hand", detail: ".github/skill-profiles.toml" }
    ],
    items: [
      {
        title: "Test a skill on a case made for it",
        body: "Some cases ship their own skill (PR review, security audit, containment). Try it as it is, improve it, or bring yours — the same A/B, from the case.",
        status: ["ui"],
        to: "/evidence/cases?family=security&case=pr-review-v1"
      },
      {
        title: "Try a skill on top of minimal",
        body: "Minimal against minimal plus one skill, with use enforced. The narrowest and most useful test. Created here, launched from Experiments.",
        status: ["ui", "cli"],
        to: "/evidence/skills?tab=try",
        command: "make add-skill-trial SKILL=<name> MODEL=<model> CASES=\"a b c\""
      },
      {
        title: "Minimal vs full",
        body: "One run: pick the skills profile in Runs. A full experiment across models is still terminal-only.",
        status: ["ui", "cli"],
        to: "/advanced/runs",
        command: "make skill-profile-experiment MODELS=\"…\" CASES=\"…\""
      },
      {
        title: "Add or remove skills",
        body: "A single-factor experiment: add draft skills or remove catalog skills.",
        status: ["ui"],
        to: "/evaluate/new"
      },
      {
        title: "Import or stage a skill",
        body: "From GitHub, a SKILL.md or a .zip. You see a preview before anything is written.",
        status: ["ui"],
        to: "/evidence/skills?tab=import"
      },
      {
        title: "Prune the harness",
        body: "Removes skills in groups and measures whether quality drops. Verdicts show in Improvements.",
        status: ["cli"],
        command: "make prune MODEL=<model> CASES=\"…\""
      },
      {
        title: "Refine a skill",
        body: "The current version against a candidate, on the same cases.",
        status: ["cli"],
        command: "make refine-skill SKILL=<name> CANDIDATE=<name>-v2"
      },
      {
        title: "Which skills get used",
        body: "Census: which skills the agent opened, by model, case or harness.",
        status: ["ui"],
        to: "/evidence/skills?tab=usage"
      }
    ],
    levels: "The two levels",
    levelsSource: "defined in .github/skill-profiles.toml",
    skillsCount: (n) => `${n} skills`,
    hiddenInMinimal: (n) => `+ ${n} optional, hidden in minimal`,
    lastPrune: "Latest pruning verdict",
    lastPruneMeta: (model, cases, runs) => `${model} · ${cases} cases · ${runs} runs`,
    decision: {
      removable: "removable",
      keep: "keep",
      inconclusive: "inconclusive",
      no_signal: "no signal"
    },
    interval: (lo, hi) => `success difference interval [${lo}, ${hi}]`,
    openImprovements: "All pruning verdicts",
    footnote:
      "Without Enforce, agents rarely open a skill on their own, so a trial that does not ask for it mostly measures nothing."
  },
  es: {
    title: "Arnés y skills",
    lede: "Dos niveles de arnés y una pregunta por skill: ¿mejora el resultado o solo ocupa contexto?",
    flow: [
      { title: "Elegí una skill", detail: "del catálogo o importada" },
      { title: "Minimal vs minimal + skill", detail: "con uso exigido" },
      { title: "Leé el veredicto", detail: "casos objetivo y uno general" },
      { title: "Promovela a mano", detail: ".github/skill-profiles.toml" }
    ],
    items: [
      {
        title: "Probá una skill en un caso hecho para ella",
        body: "Algunos casos traen su propia skill (PR review, auditoría de seguridad, contención). Probala como está, mejorala o traé la tuya: el mismo A/B, desde el caso.",
        status: ["ui"],
        to: "/evidence/cases?family=security&case=pr-review-v1"
      },
      {
        title: "Probá una skill sobre minimal",
        body: "Minimal contra minimal más una skill, con uso exigido. La prueba más angosta y la más útil. Se crea acá y se lanza desde Experimentos.",
        status: ["ui", "cli"],
        to: "/evidence/skills?tab=try",
        command: "make add-skill-trial SKILL=<nombre> MODEL=<modelo> CASES=\"a b c\""
      },
      {
        title: "Minimal vs full",
        body: "Una corrida: elegí el perfil de skills en Corridas. Un experimento completo entre modelos, por ahora solo por terminal.",
        status: ["ui", "cli"],
        to: "/advanced/runs",
        command: "make skill-profile-experiment MODELS=\"…\" CASES=\"…\""
      },
      {
        title: "Agregá o quitá skills",
        body: "Experimento de un solo factor: agregar skills en borrador o quitar skills del catálogo.",
        status: ["ui"],
        to: "/evaluate/new"
      },
      {
        title: "Importá o prepará una skill",
        body: "Desde GitHub, un SKILL.md o un .zip. Ves la vista previa antes de que se escriba nada.",
        status: ["ui"],
        to: "/evidence/skills?tab=import"
      },
      {
        title: "Podá el arnés",
        body: "Quita skills de a grupos y mide si se pierde calidad. Los veredictos se ven en Mejoras.",
        status: ["cli"],
        command: "make prune MODEL=<modelo> CASES=\"…\""
      },
      {
        title: "Refiná una skill",
        body: "La versión actual contra una candidata, sobre los mismos casos.",
        status: ["cli"],
        command: "make refine-skill SKILL=<nombre> CANDIDATE=<nombre>-v2"
      },
      {
        title: "Qué skills se usan",
        body: "Censo: qué skills abrió el agente, por modelo, caso o arnés.",
        status: ["ui"],
        to: "/evidence/skills?tab=usage"
      }
    ],
    levels: "Los dos niveles",
    levelsSource: "definidos en .github/skill-profiles.toml",
    skillsCount: (n) => `${n} skills`,
    hiddenInMinimal: (n) => `+ ${n} opcionales, ocultas en minimal`,
    lastPrune: "Último veredicto de poda",
    lastPruneMeta: (model, cases, runs) => `${model} · ${cases} casos · ${runs} corridas`,
    decision: {
      removable: "se puede quitar",
      keep: "conservar",
      inconclusive: "no concluyente",
      no_signal: "sin señal"
    },
    interval: (lo, hi) => `intervalo de la diferencia de éxito [${lo}, ${hi}]`,
    openImprovements: "Todos los veredictos de poda",
    footnote:
      "Sin «Exigir el uso», los agentes casi nunca abren una skill por su cuenta: una prueba que no la pide mide casi nada."
  }
});

function signed(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? "+" : ""}${rounded.toFixed(2)}`;
}

export function HarnessHub() {
  const m = useMessages(MESSAGES);
  const [profiles, setProfiles] = useState<Record<string, SkillProfileRecord> | null>(null);
  const [pruning, setPruning] = useState<PruningOverviewRow[]>([]);

  useEffect(() => {
    let active = true;
    api
      .listSkillProfiles()
      .then((rows) => active && setProfiles(rows))
      .catch(() => undefined);
    api
      .listPruning()
      .then((rows) => active && setPruning(rows))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  // The most recent finished pruning experiment with a verdict, read live.
  const latest = useMemo(() => {
    const finished = pruning
      .filter((row) => row.finished && row.verdicts.length > 0)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
    return finished[0] ?? null;
  }, [pruning]);

  const minimal = profiles?.minimal;
  const full = profiles?.full;

  return (
    <HubPage
      flow={m.flow}
      footnote={m.footnote}
      icon={IconLayers}
      items={m.items}
      lede={m.lede}
      title={m.title}
    >
      {minimal && full ? (
        <section className="panel hub-levels">
          <div className="panel-head">
            <h2>{m.levels}</h2>
            <span className="muted">{m.levelsSource}</span>
          </div>
          <div className="hub-level">
            <strong>
              minimal <small className="muted">{m.skillsCount(minimal.skills.length)}</small>
            </strong>
            <div className="hub-pills">
              {minimal.skills.map((skill) => (
                <code className="hub-pill" key={skill}>
                  {skill}
                </code>
              ))}
            </div>
          </div>
          <div className="hub-level">
            <strong>
              full <small className="muted">{m.skillsCount(full.skills.length)}</small>
            </strong>
            <div className="hub-pills">
              <code className="hub-pill">minimal</code>
              <code className="hub-pill hub-pill--more">
                {m.hiddenInMinimal(minimal.disabled.length)}
              </code>
            </div>
          </div>
          {latest ? (
            <div className="hub-verdict">
              <div className="panel-head">
                <h3>{m.lastPrune}</h3>
                <span className="muted">
                  {m.lastPruneMeta(latest.model ?? "—", latest.case_ids.length, latest.runs)}
                </span>
              </div>
              {latest.verdicts.slice(0, 1).map((verdict) => (
                <p key={verdict.arm}>
                  <span className={verdict.decision === "removable" ? "badge ok" : "badge neutral"}>
                    {m.decision[verdict.decision]}
                  </span>{" "}
                  {verdict.reason}
                  {verdict.success_interval ? (
                    <span className="muted">
                      {" · "}
                      {m.interval(
                        signed(verdict.success_interval[0]),
                        signed(verdict.success_interval[1])
                      )}
                    </span>
                  ) : null}
                </p>
              ))}
              <Link className="button-link" to="/advanced/improvements">
                {m.openImprovements}
              </Link>
            </div>
          ) : null}
        </section>
      ) : null}
    </HubPage>
  );
}
