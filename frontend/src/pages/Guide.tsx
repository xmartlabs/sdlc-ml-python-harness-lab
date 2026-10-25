import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api";
import { Markdown } from "../components/Markdown";
import { defineMessages, useMessages } from "../i18n";
import type { DocContent, DocEntry } from "../types";

// The loop at a glance; each step is a journey in the index below.
const CORE_LOOP_EN = `
**Case → run → read → keep what works.** Make a case whose check the agent did not write, run it under configurations that differ in one thing (model, harness, skill, prompt), read the verdict with its interval and validity banner, then copy what helped into your project (\`make harness-export\`) or into the template. Start with the first document below; the six journeys after it are one page each.
`;

const CORE_LOOP_ES = `
**Caso → corrida → lectura → quedate con lo que funciona.** Armá un caso cuyo chequeo no haya escrito el agente, corrélo con configuraciones que difieran en una sola cosa (modelo, arnés, skill, prompt), leé el veredicto con su intervalo y su aviso de validez, y después copiá lo que ayudó a tu proyecto (\`make harness-export\`) o al template. Empezá por el primer documento de la lista; los seis recorridos que siguen ocupan una página cada uno. Los documentos están en inglés.
`;

const MESSAGES = defineMessages({
  en: {
    eyebrow: "Guide",
    title: "How to use the lab",
    lede: "Everything you need to run experiments, compare harnesses, and feed improvements back to your repos — the real docs, rendered here.",
    coreLoopTitle: "The loop in one line",
    coreLoop: CORE_LOOP_EN,
    docsUnavailable: "Docs API unavailable — start the app backend.",
    selectGuide: "Select a guide to read it here."
  },
  es: {
    eyebrow: "Guía",
    title: "Cómo usar el lab",
    lede: "Todo lo que necesitás para correr experimentos, comparar arneses y devolver mejoras a tus repositorios — los documentos reales, mostrados acá.",
    coreLoopTitle: "El ciclo en una línea",
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

  // A link from one doc to another (`journey-run-and-compare.md`,
  // `x.md#section`) opens that doc here when it is catalogued; anything else
  // keeps its normal behaviour.
  const openDocLink = (href: string): boolean => {
    // Only links inside docs/: `../README.md` is the repository's README, not
    // docs/README.md, and shares its file name.
    if (href.startsWith("../") || href.startsWith("/")) return false;
    const file = href.split("#")[0].split("/").pop() ?? "";
    const match = docs.find((doc) => doc.file === file);
    if (!match) return false;
    setSelected(match.slug);
    window.scrollTo({ top: 0 });
    return true;
  };

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
            <Markdown onDocLink={openDocLink}>{content.content}</Markdown>
          ) : (
            <p className="muted">{m.selectGuide}</p>
          )}
        </div>
      </div>
    </section>
  );
}
