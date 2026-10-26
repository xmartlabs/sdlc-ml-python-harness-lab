import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { defineMessages, useMessages } from "../i18n";
import { IconChevronRight } from "./icons";

const MESSAGES = defineMessages({
  en: { openGuide: "Open the guide" },
  es: { openGuide: "Abrir la guía" }
});

type HelpPanelProps = {
  title: string;
  /** The Guide doc the panel expands on (its slug); "Open the guide" opens it. */
  doc?: string;
  defaultOpen?: boolean;
  children: ReactNode;
};

// Collapsible in-app onboarding block. Mirrors the Skills "how-to" pattern and
// links to the in-app guide so the UI stays self-explanatory.
export function HelpPanel({ title, doc, defaultOpen = false, children }: HelpPanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const m = useMessages(MESSAGES);
  return (
    <section className="panel help-panel">
      <div className="help-head">
        <button
          aria-expanded={open}
          className="help-toggle"
          onClick={() => setOpen((value) => !value)}
          type="button"
        >
          <span className="help-toggle-label">
            <span className="help-toggle-chevron">
              <IconChevronRight size={14} />
            </span>
            {title}
          </span>
        </button>
        {doc ? (
          <Link className="help-doc-link" to={`/system/guide?doc=${doc}`}>
            {m.openGuide}
          </Link>
        ) : null}
      </div>
      {open ? <div className="help-body">{children}</div> : null}
    </section>
  );
}
