import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api";
import { Markdown } from "../components/Markdown";
import { defineMessages, useMessages } from "../i18n";
import type { DocContent, DocEntry } from "../types";

// The core loop shown at the top so the web is self-explanatory at a glance.
const CORE_LOOP_EN = `
1. **Start from a question** on Home — validate a tool you built, evaluate a harness change, or audit a repository you have never seen.
2. **Declare the design** — cases, arms, repetitions. The lab refuses a design its mode cannot answer.
3. **Runs execute in containers.** A case with a validation command scores itself; a case with a rubric is LLM-judged; anything else you score from the run page.
4. **Read the experiment** — one rate per arm, the case matrix, and the verdict's caveats. Then record a finding.
5. **Feed it back** — a run's audit becomes a sanitised issue for the governance template (\`make improve RUN=<id>\`).

Audits, judges and summaries use your AI gateway, or the Claude Code CLI when only a Claude credential is configured (Health → LLM transport).
`;

const CORE_LOOP_ES = `
1. **Arrancá de una pregunta** en Inicio — validá una herramienta que construiste, evaluá un cambio de arnés, o auditá un repositorio que nunca viste.
2. **Declará el diseño** — casos, brazos, repeticiones. El lab rechaza un diseño que su modo no puede responder.
3. **Las corridas se ejecutan en contenedores.** Un caso con un comando de validación se puntúa solo; un caso con una rúbrica lo juzga un LLM; cualquier otro lo puntuás vos desde la página de la corrida.
4. **Leé el experimento** — una tasa por brazo, la matriz de casos y las salvedades del veredicto. Después registrá un hallazgo.
5. **Devolvé lo aprendido** — la auditoría de una corrida se convierte en un issue saneado para el template de gobernanza (\`make improve RUN=<id>\`).

Las auditorías, jueces y resúmenes usan tu gateway de IA, o la CLI de Claude Code cuando solo hay configurada una credencial de Claude (Estado → transporte LLM).
`;

const MESSAGES = defineMessages({
  en: {
    eyebrow: "Guide",
    title: "How to use the lab",
    lede: "Everything you need to run experiments, compare harnesses, and feed improvements back to your repos — the real docs, rendered here.",
    coreLoopTitle: "The core loop",
    coreLoop: CORE_LOOP_EN,
    docsUnavailable: "Docs API unavailable — start the app backend.",
    selectGuide: "Select a guide to read it here."
  },
  es: {
    eyebrow: "Guía",
    title: "Cómo usar el lab",
    lede: "Todo lo que necesitás para correr experimentos, comparar arneses y devolver mejoras a tus repositorios — los documentos reales, mostrados acá.",
    coreLoopTitle: "El ciclo central",
    coreLoop: CORE_LOOP_ES,
    docsUnavailable: "La API de documentos no está disponible — iniciá el backend de la app.",
    selectGuide: "Elegí una guía para leerla acá."
  }
});

export function Guide() {
  // `?doc=<slug>` opens one doc directly, so Home can link to the demo guide.
  const [searchParams] = useSearchParams();
  const [docs, setDocs] = useState<DocEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(searchParams.get("doc"));
  const [content, setContent] = useState<DocContent | null>(null);
  // A flag, not translated text: resolved at render time so a language switch
  // after the failed load still shows the right string.
  const [loadFailed, setLoadFailed] = useState(false);
  const m = useMessages(MESSAGES);

  useEffect(() => {
    let active = true;
    api
      .listDocs()
      .then((entries) => {
        if (!active) return;
        setDocs(entries);
        // An unknown `?doc=` falls back to the first doc rather than a blank page.
        setSelected((current) =>
          current && entries.some((entry) => entry.slug === current)
            ? current
            : (entries[0]?.slug ?? null)
        );
      })
      .catch(() => active && setLoadFailed(true));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setContent(null);
    api
      .getDoc(selected)
      .then((doc) => active && setContent(doc))
      .catch(() => active && setContent(null));
    return () => {
      active = false;
    };
  }, [selected]);

  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup: Record<string, DocEntry[]> = {};
    for (const doc of docs) {
      if (!byGroup[doc.group]) {
        byGroup[doc.group] = [];
        order.push(doc.group);
      }
      byGroup[doc.group].push(doc);
    }
    return order.map((group) => ({ group, items: byGroup[group] }));
  }, [docs]);

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{m.eyebrow}</p>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.coreLoopTitle}</h2>
        </div>
        <Markdown>{m.coreLoop}</Markdown>
      </section>

      {loadFailed ? <p className="muted">{m.docsUnavailable}</p> : null}

      <div className="guide-layout">
        <nav className="guide-index">
          {groups.map(({ group, items }) => (
            <div className="guide-group" key={group}>
              <p className="guide-group-title">{group}</p>
              {items.map((doc) => (
                <button
                  className={
                    doc.slug === selected ? "guide-link guide-link--active" : "guide-link"
                  }
                  key={doc.slug}
                  onClick={() => setSelected(doc.slug)}
                  type="button"
                >
                  <span className="guide-link-title">{doc.title}</span>
                  <span className="guide-link-desc">{doc.description}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="panel guide-content">
          {content ? (
            <Markdown>{content.content}</Markdown>
          ) : (
            <p className="muted">{m.selectGuide}</p>
          )}
        </div>
      </div>
    </section>
  );
}
