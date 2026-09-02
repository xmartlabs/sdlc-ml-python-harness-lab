import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import {
  IconAlert,
  IconArrowRight,
  IconCheck,
  IconFlask,
  IconInfo,
  IconLayers,
  IconSearch
} from "../components/icons";
import type { IconProps } from "../components/icons";
import type { AttentionFeed, AttentionItem, EvidenceSummary } from "../types";
import type { ComponentType } from "react";

/**
 * The three questions this lab can answer, as the way in.
 *
 * The old home page opened with a leaderboard, which answered "what data exists
 * in the system?" — a question nobody arrives with. These are the questions
 * people actually arrive with, and each one maps to an evaluation mode whose
 * design constraints are enforced downstream (ADR-0028).
 */
const MODES = [
  {
    key: "harness_evaluation",
    icon: IconFlask,
    title: "Evaluate a harness change",
    question:
      "Does this skill, rule, workflow, prompt or harness change actually improve performance?",
    cta: "Start harness evaluation",
    to: "/evaluate/new?mode=harness_evaluation",
    holds: ["Where it runs", "Cases", "Model"],
    varies: "Harness condition",
    note: "Works on this lab's own harness — the continuous-improvement loop — or on an external repository. The only mode that can support a causal claim, and only with enough repetitions per arm."
  },
  {
    key: "cross_repo",
    icon: IconLayers,
    title: "Test across repositories",
    question: "Does this harness generalize across different projects or teams?",
    cta: "Start cross-repo evaluation",
    to: "/evaluate/new?mode=cross_repo",
    holds: ["Harness", "Model"],
    varies: "Repository",
    note: "Results stay per repository. Pooling them would treat different codebases as one condition."
  },
  {
    key: "discovery",
    icon: IconSearch,
    title: "Audit a repository",
    question: "What can we learn from how this project or team currently works with AI?",
    cta: "Start repository audit",
    to: "/evaluate/audit",
    holds: [],
    varies: "Nothing — this is exploratory",
    note: "Produces cases, observations and suggested findings. It never produces a causal verdict."
  }
] as const;

const SEVERITY_ICON: Record<string, ComponentType<IconProps>> = {
  critical: IconAlert,
  warning: IconAlert,
  info: IconInfo
};

const ONBOARDING = [
  "Add a repository to evaluate against.",
  "Create cases by hand, or derive them from the repository's own history.",
  "Choose an evaluation mode above.",
  "Run the experiment and read the case matrix, not just the average."
];

// Both guard against `undefined`, not just `null`. A number this page cannot
// find must render as an em dash — a home page that throws because one metric
// is missing takes the whole app down with it.
function formatPercent(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

function formatMoney(value: number | null | undefined): string {
  return typeof value === "number" ? `$${value.toFixed(2)}` : "—";
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const Icon = SEVERITY_ICON[item.severity] ?? IconInfo;
  return (
    <li className={`attention-item attention-item--${item.severity}`}>
      <span className="attention-item-icon">
        <Icon size={18} />
      </span>
      <div className="attention-item-body">
        <strong>{item.headline}</strong>
        <p className="muted">{item.detail}</p>
      </div>
      <Link className="secondary attention-item-action" to={item.route}>
        {item.action}
        <IconArrowRight size={14} />
      </Link>
    </li>
  );
}

export function Home() {
  const [attention, setAttention] = useState<AttentionFeed | null>(null);
  const [summary, setSummary] = useState<EvidenceSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([api.getAttention(), api.getEvidenceSummary()])
      .then(([feed, evidence]) => {
        if (!active) {
          return;
        }
        setAttention(feed);
        setSummary(evidence);
        setError(null);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(
            loadError instanceof Error ? loadError.message : "Unable to reach the lab"
          );
        }
      })
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  // An empty lab gets the four steps; a lab in use does not need them taking up
  // the space where its own evidence should be.
  const isNewLab =
    summary !== null && summary.repositories === 0 && summary.cases === 0;

  return (
    <section className="page home">
      <header className="page-header">
        <div>
          <h1>What do you want to learn?</h1>
          <p className="lede">
            An evaluation instrument for AI-assisted software engineering. Start from
            a question; the lab holds you to a design that can answer it.
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} /> : null}

      <div className="mode-cards">
        {MODES.map((mode) => {
          const Icon = mode.icon;
          return (
            <article className="mode-card" key={mode.key}>
              <span className="mode-card-icon">
                <Icon size={22} />
              </span>
              <h2>{mode.title}</h2>
              <p className="mode-card-question">{mode.question}</p>
              <dl className="mode-card-design">
                {mode.holds.length > 0 ? (
                  <div>
                    <dt>Held fixed</dt>
                    <dd>{mode.holds.join(" · ")}</dd>
                  </div>
                ) : null}
                <div>
                  <dt>Varies</dt>
                  <dd>{mode.varies}</dd>
                </div>
              </dl>
              <p className="mode-card-note muted">{mode.note}</p>
              <Link className="primary mode-card-cta" to={mode.to}>
                {mode.cta}
                <IconArrowRight size={15} />
              </Link>
            </article>
          );
        })}
      </div>

      {isNewLab ? (
        <section className="panel subtle onboarding">
          <h2>New to the lab?</h2>
          <ol className="onboarding-steps">
            {ONBOARDING.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      ) : null}

      <section className="panel attention">
        <div className="panel-head">
          <h2>What needs attention?</h2>
        </div>
        {isLoading ? <Spinner label="Reading the corpus" /> : null}
        {!isLoading && attention?.clear ? (
          <p className="attention-clear">
            <IconCheck size={16} />
            Nothing is waiting on you. Every experiment has enough repetitions, every
            finished run is scored, and no case has regressed.
          </p>
        ) : null}
        {attention && attention.items.length > 0 ? (
          <ul className="attention-list">
            {attention.items.map((item) => (
              <AttentionRow item={item} key={item.kind} />
            ))}
          </ul>
        ) : null}
      </section>

      {summary ? (
        <section className="panel subtle evidence-summary">
          <div className="panel-head">
            <h2>What the lab holds</h2>
            <Link className="button-link" to="/advanced/benchmarks">
              Full leaderboard
            </Link>
          </div>
          <div className="metrics">
            <div className="metric">
              <span>Active experiments</span>
              <strong>{summary.experiments_active}</strong>
            </div>
            <div className="metric">
              <span>Repositories</span>
              <strong>{summary.repositories}</strong>
            </div>
            <div className="metric">
              <span>Cases</span>
              <strong>{summary.cases}</strong>
            </div>
            <div className="metric">
              <span>Success rate ({summary.window_days}d)</span>
              <strong>{formatPercent(summary.success.rate)}</strong>
              <small className="muted">
                {summary.success.n === 0
                  ? "No validated runs yet"
                  : `over ${summary.success.n} validated run(s)`}
              </small>
            </div>
            <div className="metric">
              <span>Median run cost</span>
              <strong>{formatMoney(summary.cost.median_usd)}</strong>
            </div>
            <div className="metric">
              <span>Open findings</span>
              <strong>{summary.findings_open}</strong>
            </div>
          </div>
        </section>
      ) : null}
    </section>
  );
}
