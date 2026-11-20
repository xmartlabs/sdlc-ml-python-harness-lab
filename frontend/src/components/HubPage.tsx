import type { ComponentType, ReactNode } from "react";
import { Link } from "react-router-dom";

import { defineMessages, useMessages } from "../i18n";
import { IconArrowRight } from "./icons";
import type { IconProps } from "./icons";

/**
 * A capability hub (ADR-0090): one entry of the navigation, as a board of cards.
 *
 * Each card says what a capability does, whether it is in the UI, terminal-only
 * or still to build, and where to go. Hubs only link; every screen they point
 * to is unchanged, at the route it always had.
 */

export type CapabilityStatus = "ui" | "cli" | "todo";

export type Capability = {
  title: string;
  body: string;
  /** One or more: a capability can be half in the UI and half in the terminal. */
  status: CapabilityStatus[];
  /** In-app route the card opens. */
  to?: string;
  /** The `make` command, for terminal-only capabilities. */
  command?: string;
  /** Short monospace tags, e.g. case ids. */
  tags?: string[];
};

export type FlowStep = { title: string; detail: string };

const MESSAGES = defineMessages({
  en: {
    status: { ui: "In the UI", cli: "Terminal only", todo: "To build" },
    open: "Open",
    legend: "Each card says whether it is built and where it lives."
  },
  es: {
    status: { ui: "En la UI", cli: "Solo terminal", todo: "Por sumar" },
    open: "Abrir",
    legend: "Cada tarjeta dice si ya está construida y dónde vive."
  }
});

export function StatusChip({ status }: { status: CapabilityStatus }) {
  const m = useMessages(MESSAGES);
  return <span className={`hub-chip hub-chip--${status}`}>{m.status[status]}</span>;
}

/**
 * A row of numbered steps. With `current` (0-based), it is a progress strip:
 * the steps before it are done (✓), it is lit, the rest wait. Without it, a
 * static map, as every hub drew it before.
 */
export function FlowDiagram({ steps, current }: { steps: FlowStep[]; current?: number }) {
  return (
    <ol className={current === undefined ? "hub-flow" : "hub-flow hub-flow--progress"}>
      {steps.map((step, index) => {
        const state =
          current === undefined ? "" : index < current ? "done" : index === current ? "current" : "todo";
        return (
          <li
            aria-current={state === "current" ? "step" : undefined}
            className={state ? `hub-flow-step hub-flow-step--${state}` : "hub-flow-step"}
            key={step.title}
          >
            <span className="hub-flow-n">{state === "done" ? "✓" : index + 1}</span>
            <strong>{step.title}</strong>
            {step.detail ? <span className="muted">{step.detail}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

export function CapabilityCard({ item }: { item: Capability }) {
  const m = useMessages(MESSAGES);
  const isTodo = item.status.length === 1 && item.status[0] === "todo";
  return (
    <article className={isTodo ? "hub-card hub-card--todo" : "hub-card"}>
      <div className="hub-card-chips">
        {item.status.map((status) => (
          <StatusChip key={status} status={status} />
        ))}
      </div>
      <h3>{item.title}</h3>
      <p className="muted">{item.body}</p>
      {item.tags && item.tags.length > 0 ? (
        <div className="hub-card-tags">
          {item.tags.map((tag) => (
            <code key={tag}>{tag}</code>
          ))}
        </div>
      ) : null}
      {item.command ? <pre className="hub-card-command"><code>{item.command}</code></pre> : null}
      {item.to ? (
        <Link className="hub-card-link" to={item.to}>
          {m.open}
          <IconArrowRight size={14} />
        </Link>
      ) : null}
    </article>
  );
}

type HubPageProps = {
  icon: ComponentType<IconProps>;
  title: string;
  lede: string;
  flow?: FlowStep[];
  items: Capability[];
  /** Extra content between the flow and the cards (e.g. live profile data). */
  children?: ReactNode;
  footnote?: string;
};

export function HubPage({ icon: Icon, title, lede, flow, items, children, footnote }: HubPageProps) {
  const m = useMessages(MESSAGES);
  return (
    <section className="page hub">
      <header className="page-header">
        <div>
          <p className="eyebrow hub-eyebrow">
            <Icon size={14} />
            {title}
          </p>
          <h1>{title}</h1>
          <p className="lede">{lede}</p>
        </div>
      </header>
      {flow ? <FlowDiagram steps={flow} /> : null}
      {children}
      <div className="hub-legend">
        <StatusChip status="ui" />
        <StatusChip status="cli" />
        <StatusChip status="todo" />
        <span className="muted">{m.legend}</span>
      </div>
      <div className="hub-cards">
        {items.map((item) => (
          <CapabilityCard item={item} key={item.title} />
        ))}
      </div>
      {footnote ? <p className="hub-footnote muted">{footnote}</p> : null}
    </section>
  );
}
