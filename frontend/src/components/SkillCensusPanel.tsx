import { useEffect, useState } from "react";

import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { SkillCensusRecord } from "../types";

/**
 * The skill census (ADR-0085): per model, case or harness CLI, which governed
 * skills the measured runs actually opened, beside what each one costs to
 * carry. Co-occurrence only — it orders what to ablate (make prune), it never
 * removes anything by itself.
 */

type Dimension = SkillCensusRecord["by"];
const DIMENSIONS: Dimension[] = ["model", "case", "harness"];
const SKILL_PREFIX = "skill:";

const MESSAGES = defineMessages({
  en: {
    heading: "Skill census",
    intro:
      "Which skills the per-step-measured runs opened, per group, beside what each costs: the description is paid by every run, the body only when the skill is opened. Never-opened skills are the first pruning candidates — co-occurrence, not cause; ablate before removing.",
    by: { model: "By model", case: "By case", harness: "By harness" } as Record<Dimension, string>,
    group: "Group",
    runsMeasured: (n: number) => `${n} measured runs`,
    skill: "Skill",
    kind: "Kind",
    opened: "Opened in",
    reward: "Reward when opened",
    description: "Description chars",
    body: "Body",
    never: "never",
    summary: (unopened: number, total: number, chars: number) =>
      `${unopened} of ${total} skills never opened here — ${chars.toLocaleString()} description chars carried by every run for nothing.`,
    empty: "No per-step-measured runs yet. Run a case whose validation records progress per step.",
    thin: "Fewer than 10 measured runs: read this as a hint, not a census.",
    next: "Next step: make prune GROUP=1 MODEL=<model> CASES=… ITEMS=<never-opened skills>"
  },
  es: {
    heading: "Censo de skills",
    intro:
      "Qué skills abrieron las corridas medidas paso a paso, por grupo, junto a lo que cuesta cada una: la descripción la paga toda corrida, el cuerpo solo cuando se abre. Las nunca abiertas son las primeras candidatas a podar — coocurrencia, no causa; ablacioná antes de sacar.",
    by: { model: "Por modelo", case: "Por caso", harness: "Por arnés" } as Record<Dimension, string>,
    group: "Grupo",
    runsMeasured: (n: number) => `${n} corridas medidas`,
    skill: "Skill",
    kind: "Tipo",
    opened: "Abierta en",
    reward: "Recompensa al abrirla",
    description: "Caracteres de descripción",
    body: "Cuerpo",
    never: "nunca",
    summary: (unopened: number, total: number, chars: number) =>
      `${unopened} de ${total} skills nunca se abrieron acá — ${chars.toLocaleString()} caracteres de descripción que carga cada corrida sin usarlos.`,
    empty: "Todavía no hay corridas medidas paso a paso. Corré un caso cuya validación registre progreso por paso.",
    thin: "Menos de 10 corridas medidas: leelo como una pista, no como un censo.",
    next: "Siguiente paso: make prune GROUP=1 MODEL=<modelo> CASES=… ITEMS=<skills nunca abiertas>"
  }
});

const THIN_CENSUS = 10;

function kilobytes(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

export function SkillCensusPanel() {
  const m = useMessages(MESSAGES);
  const [by, setBy] = useState<Dimension>("model");
  const [census, setCensus] = useState<SkillCensusRecord | null>(null);
  const [group, setGroup] = useState<string>("");

  useEffect(() => {
    let active = true;
    api
      .getSkillCensus(by)
      .then((record) => {
        if (!active) return;
        setCensus(record);
        setGroup((current) =>
          current in record.groups ? current : Object.keys(record.groups)[0] ?? ""
        );
      })
      .catch(() => {
        if (active) setCensus(null);
      });
    return () => {
      active = false;
    };
  }, [by]);

  const selected = census?.groups[group];
  const rows = (selected?.items ?? []).filter((row) => row.item.startsWith(SKILL_PREFIX));
  const unopened = rows.filter((row) => row.runs === 0);
  const unopenedChars = unopened.reduce(
    (sum, row) =>
      sum + (census?.footprints[row.item.slice(SKILL_PREFIX.length)]?.description_chars ?? 0),
    0
  );

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{m.heading}</h2>
        <span className="muted">make skill-census</span>
      </div>
      <p className="muted small">{m.intro}</p>
      <div className="toolbar">
        {DIMENSIONS.map((dimension) => (
          <button
            className={dimension === by ? "primary" : "secondary"}
            key={dimension}
            onClick={() => setBy(dimension)}
            type="button"
          >
            {m.by[dimension]}
          </button>
        ))}
        {census && Object.keys(census.groups).length > 0 ? (
          <label>
            {m.group}{" "}
            <select onChange={(event) => setGroup(event.target.value)} value={group}>
              {Object.entries(census.groups).map(([key, value]) => (
                <option key={key} value={key}>
                  {key} ({value.runs_measured})
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {!selected ? (
        <p className="muted">{m.empty}</p>
      ) : (
        <>
          <p>
            <strong>{m.runsMeasured(selected.runs_measured)}</strong> ·{" "}
            {m.summary(unopened.length, rows.length, unopenedChars)}
          </p>
          {selected.runs_measured < THIN_CENSUS ? (
            <div className="callout callout--warning small">
              <p>{m.thin}</p>
            </div>
          ) : null}
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.skill}</th>
                <th>{m.kind}</th>
                <th>{m.opened}</th>
                <th>{m.reward}</th>
                <th>{m.description}</th>
                <th>{m.body}</th>
              </tr>
            </thead>
            <tbody>
              {[...rows].reverse().map((row) => {
                const name = row.item.slice(SKILL_PREFIX.length);
                const footprint = census?.footprints[name];
                return (
                  <tr key={row.item}>
                    <td className="mono small">{name}</td>
                    <td>
                      <span className="badge neutral">{footprint?.kind ?? "—"}</span>
                    </td>
                    <td>
                      {row.runs === 0 ? (
                        <span className="badge warn">{m.never}</span>
                      ) : (
                        `${row.runs} (${Math.round(row.share_of_runs * 100)}%)`
                      )}
                    </td>
                    <td>{row.runs === 0 ? "—" : row.reward_when_consulted.toFixed(2)}</td>
                    <td>{footprint?.description_chars ?? "—"}</td>
                    <td>{footprint ? kilobytes(footprint.body_bytes) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted small">{m.next}</p>
        </>
      )}
    </section>
  );
}
