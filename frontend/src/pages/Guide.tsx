import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api";
import { Markdown } from "../components/Markdown";
import type { DocContent, DocEntry } from "../types";

// The core loop shown at the top so the web is self-explanatory at a glance.
const CORE_LOOP = `
1. **Start from a question** on Home — validate a tool you built, evaluate a harness change, or audit a repository you have never seen.
2. **Declare the design** — cases, arms, repetitions. The lab refuses a design its mode cannot answer.
3. **Runs execute in containers.** A case with a validation command scores itself; a case with a rubric is LLM-judged; anything else you score from the run page.
4. **Read the experiment** — one rate per arm, the case matrix, and the verdict's caveats. Then record a finding.
5. **Feed it back** — a run's audit becomes a sanitised issue for the governance template (\`make improve RUN=<id>\`).

Audits, judges and summaries use your AI gateway, or the Claude Code CLI when only a Claude credential is configured (Health → LLM transport).
`;

export function Guide() {
  // `?doc=<slug>` opens one doc directly, so Home can link to the demo guide.
  const [searchParams] = useSearchParams();
  const [docs, setDocs] = useState<DocEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(searchParams.get("doc"));
  const [content, setContent] = useState<DocContent | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

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
      .catch(() => active && setLoadError("Docs API unavailable — start the app backend."));
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
          <p className="eyebrow">Guide</p>
          <h1>How to use the lab</h1>
          <p className="lede">
            Everything you need to run experiments, compare harnesses, and feed
            improvements back to your repos — the real docs, rendered here.
          </p>
        </div>
      </header>

      <section className="panel">
        <div className="panel-head">
          <h2>The core loop</h2>
        </div>
        <Markdown>{CORE_LOOP}</Markdown>
      </section>

      {loadError ? <p className="muted">{loadError}</p> : null}

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
            <p className="muted">Select a guide to read it here.</p>
          )}
        </div>
      </div>
    </section>
  );
}
