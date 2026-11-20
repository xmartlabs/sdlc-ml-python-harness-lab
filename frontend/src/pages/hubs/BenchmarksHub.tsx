import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api";
import { CASE_FAMILIES, groupByFamily, isPlayable, needsOwnImage } from "../../caseFamilies";
import type { CaseFamily } from "../../caseFamilies";
import { caseOptionLabel } from "../../caseLabels";
import { StatusChip } from "../../components/HubPage";
import { IconArrowRight, IconFiles, IconPlus } from "../../components/icons";
import { defineMessages, useMessages } from "../../i18n";
import type { CaseRecord } from "../../types";

type FamilyText = { title: string; body: string; command?: string };

const MESSAGES = defineMessages<{
  title: string;
  lede: string;
  families: Record<CaseFamily, FamilyText>;
  cases: (n: number) => string;
  more: (n: number) => string;
  ownImage: string;
  playable: string;
  browse: string;
  addTitle: string;
  addBody: string;
  addCta: string;
  empty: string;
}>({
  en: {
    title: "Benchmarks",
    lede: "Cases grouped by family. Each one carries its own check, one the agent did not write. Pick one, then run it from Models or Harness & skills.",
    families: {
      swe_bench: {
        title: "Traditional: SWE-bench",
        body: "Real issues from open-source projects. Lite and Verified import by instance id; Pro brings long tasks with one image per instance.",
        command: "make swebench-selftest INSTANCE=pallets__flask-4992"
      },
      game_3d: {
        title: "3D games",
        body: "For frontier models. Godot engine, its own image, graded in phases by driving the engine. A finished run can be played in the Play tab.",
        command: "make images-3d   # once, before running them"
      },
      security: {
        title: "Review, security & containment",
        body: "Review a PR, fix or audit a vulnerability, and see whether the agent stays in scope with a fake production token in reach. Several use a hidden grader."
      },
      web: {
        title: "Web in a browser",
        body: "Graded in a real browser, accessibility included."
      },
      build: {
        title: "Build from scratch",
        body: "A difficulty ladder (t1 → t3) and short cases. Small models already solve the easy ones, so they work as guards."
      },
      other: {
        title: "Your cases",
        body: "Cases created here: drafted with AI, from an issue or from your repository."
      }
    },
    cases: (n) => `${n} case${n === 1 ? "" : "s"}`,
    more: (n) => `+${n} more`,
    ownImage: "own image",
    playable: "playable",
    browse: "Browse",
    addTitle: "Add a case",
    addBody: "Draft one with AI, import a SWE-bench instance, or derive one from your repository.",
    addCta: "Open Cases",
    empty: "No cases yet. Seed the benchmark cases from the Cases page."
  },
  es: {
    title: "Benchmarks",
    lede: "Casos agrupados por familia. Cada uno trae su chequeo, que el agente no escribió. Elegí uno y corrélo desde Modelos o desde Arnés y skills.",
    families: {
      swe_bench: {
        title: "Tradicionales: SWE-bench",
        body: "Issues reales de proyectos open source. Lite y Verified se importan por ID; Pro trae tareas largas con una imagen por instancia.",
        command: "make swebench-selftest INSTANCE=pallets__flask-4992"
      },
      game_3d: {
        title: "Juegos 3D",
        body: "Para modelos frontera. Motor Godot, imagen propia, calificados por fases manejando el motor. Al terminar se juegan en la pestaña Jugar.",
        command: "make images-3d   # una vez, antes de correrlos"
      },
      security: {
        title: "Revisión, seguridad y contención",
        body: "Revisar un PR, arreglar o auditar una vulnerabilidad, y ver si el agente se mantiene en su alcance con un token de producción falso a mano. Varios usan un calificador oculto."
      },
      web: {
        title: "Web en navegador",
        body: "Se califican en un navegador real, incluida la accesibilidad."
      },
      build: {
        title: "Construir desde cero",
        body: "Escalera de dificultad (t1 → t3) y casos cortos. Los modelos chicos ya resuelven los fáciles: sirven de control."
      },
      other: {
        title: "Tus casos",
        body: "Casos creados acá: redactados con IA, desde un issue o desde tu repositorio."
      }
    },
    cases: (n) => `${n} caso${n === 1 ? "" : "s"}`,
    more: (n) => `+${n} más`,
    ownImage: "imagen propia",
    playable: "se puede jugar",
    browse: "Ver",
    addTitle: "Sumá un caso",
    addBody: "Redactalo con IA, importá una instancia de SWE-bench o derivalo de tu repositorio.",
    addCta: "Abrir Casos",
    empty: "Todavía no hay casos. Sembrá los casos de benchmark desde la página Casos."
  }
});

const SHOWN_PER_FAMILY = 6;

function FamilyCard({ family, items }: { family: CaseFamily; items: CaseRecord[] }) {
  const m = useMessages(MESSAGES);
  const text = m.families[family];
  const shown = items.slice(0, SHOWN_PER_FAMILY);
  const ownImage = items.some(needsOwnImage);
  const playable = items.some(isPlayable);
  return (
    <article className="hub-card">
      <div className="hub-card-chips">
        <StatusChip status="ui" />
        <span className="badge neutral">{m.cases(items.length)}</span>
        {ownImage ? <span className="badge neutral">{m.ownImage}</span> : null}
        {playable ? <span className="badge neutral">{m.playable}</span> : null}
      </div>
      <h3>{text.title}</h3>
      <p className="muted">{text.body}</p>
      <div className="hub-card-tags">
        {shown.map((item) => (
          <code key={item.case_id} title={caseOptionLabel(item)}>
            {caseOptionLabel(item).length > 42
              ? `${caseOptionLabel(item).slice(0, 40)}…`
              : caseOptionLabel(item)}
          </code>
        ))}
        {items.length > shown.length ? (
          <span className="muted">{m.more(items.length - shown.length)}</span>
        ) : null}
      </div>
      {text.command ? (
        <pre className="hub-card-command">
          <code>{text.command}</code>
        </pre>
      ) : null}
      <Link className="hub-card-link" to={`/evidence/cases?family=${family}`}>
        {m.browse}
        <IconArrowRight size={14} />
      </Link>
    </article>
  );
}

export function BenchmarksHub() {
  const m = useMessages(MESSAGES);
  const [cases, setCases] = useState<CaseRecord[] | null>(null);

  useEffect(() => {
    let active = true;
    api
      .listCases()
      .then((rows) => active && setCases(rows.filter((row) => row.status !== "archived")))
      .catch(() => active && setCases([]));
    return () => {
      active = false;
    };
  }, []);

  const groups = useMemo(() => (cases ? groupByFamily(cases) : null), [cases]);

  return (
    <section className="page hub">
      <header className="page-header">
        <div>
          <p className="eyebrow hub-eyebrow">
            <IconFiles size={14} />
            {m.title}
          </p>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>
      {groups && cases && cases.length === 0 ? <p className="muted">{m.empty}</p> : null}
      <div className="hub-cards">
        {groups
          ? CASE_FAMILIES.filter((family) => groups[family].length > 0).map((family) => (
              <FamilyCard family={family} items={groups[family]} key={family} />
            ))
          : null}
        <article className="hub-card hub-card--todo">
          <div className="hub-card-chips">
            <StatusChip status="ui" />
          </div>
          <h3>
            <IconPlus size={16} /> {m.addTitle}
          </h3>
          <p className="muted">{m.addBody}</p>
          <Link className="hub-card-link" to="/evidence/cases">
            {m.addCta}
            <IconArrowRight size={14} />
          </Link>
        </article>
      </div>
    </section>
  );
}
