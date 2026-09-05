import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { IconChevronRight } from "./icons";

type HelpPanelProps = {
  title: string;
  /** Docs path the panel expands on; kept as the link's title so callers need no change. */
  docHref?: string;
  defaultOpen?: boolean;
  children: ReactNode;
};

// Collapsible in-app onboarding block. Mirrors the Skills "how-to" pattern and
// links to the in-app guide so the UI stays self-explanatory.
export function HelpPanel({ title, docHref, defaultOpen = false, children }: HelpPanelProps) {
  const [open, setOpen] = useState(defaultOpen);
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
        {docHref ? (
          <Link className="help-doc-link" title={docHref} to="/system/guide">
            Open the guide
          </Link>
        ) : null}
      </div>
      {open ? <div className="help-body">{children}</div> : null}
    </section>
  );
}
