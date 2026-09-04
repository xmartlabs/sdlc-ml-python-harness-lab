import { useState } from "react";
import type { ReactNode } from "react";

import { IconChevronRight } from "./icons";

type HelpPanelProps = {
  title: string;
  docHref?: string;
  defaultOpen?: boolean;
  children: ReactNode;
};

// Collapsible in-app onboarding block. Mirrors the Skills "how-to" pattern and
// links to the matching docs page so the UI stays self-explanatory.
export function HelpPanel({ title, docHref, defaultOpen = false, children }: HelpPanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="panel help-panel">
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
        {docHref ? <span className="muted">{docHref}</span> : null}
      </button>
      {open ? <div className="help-body">{children}</div> : null}
    </section>
  );
}
