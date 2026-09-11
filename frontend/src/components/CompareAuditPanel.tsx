import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { CompareAuditRecord } from "../types";
import { ErrorBanner } from "./ErrorBanner";
import { Markdown } from "./Markdown";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { Spinner } from "./Spinner";

type CompareAuditPanelProps = {
  runIds: string[];
  /** False while any selected run is still executing; the backend refuses then too. */
  allFinished: boolean;
};

/**
 * An LLM reading of the comparison table, generated only when asked for.
 *
 * The table says which cell won each row; it never says whether that adds up to
 * an improvement, which dashes are "not measured" rather than zero, or which of
 * the visible differences could account for a delta. This panel asks a model
 * that question over the same evidence.
 *
 * Nothing fires on mount. An unread selection is an empty box — the 404 from
 * the GET is the normal state, not an error worth showing — and pressing the
 * button generates or replaces the reading for exactly this set of runs.
 */
export function CompareAuditPanel({ runIds, allFinished }: CompareAuditPanelProps) {
  const [audit, setAudit] = useState<CompareAuditRecord | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);

  // The server keys a comparison by the sorted run-id set, so the panel keys
  // its effects the same way: reordering the columns must not discard the
  // reading that was already written for those runs, and changing one must.
  const selectionKey = useMemo(() => [...runIds].sort().join(","), [runIds]);
  const readable = allFinished && runIds.length >= 2;

  useEffect(() => {
    setAudit(null);
    setError(null);
    setShowEvidence(false);
    if (runIds.length < 2) {
      return;
    }
    let cancelled = false;
    api
      .getCompareAudit(runIds)
      .then((record) => {
        if (!cancelled) setAudit(record);
      })
      .catch(() => {
        if (!cancelled) setAudit(null);
      });
    return () => {
      cancelled = true;
    };
    // `selectionKey` stands in for `runIds`: a new array with the same ids is
    // the same comparison and must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionKey]);

  // Poll while the worker is writing; the reading is one gateway call, so a
  // few seconds between polls is enough.
  useEffect(() => {
    if (audit?.status !== "running") {
      return;
    }
    const timer = window.setInterval(() => {
      api
        .getCompareAudit(runIds)
        .then(setAudit)
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audit?.status, selectionKey]);

  const handleRun = () => {
    setPending(true);
    setError(null);
    api
      .runCompareAudit(runIds)
      .then(() => api.getCompareAudit(runIds))
      .then(setAudit)
      .catch((runError: unknown) =>
        setError(
          runError instanceof Error
            ? runError.message
            : "Unable to read this comparison"
        )
      )
      .finally(() => setPending(false));
  };

  const label =
    audit?.status === "running"
      ? "Reading…"
      : audit?.status === "done"
        ? "Explain again"
        : "Explain this comparison";

  return (
    <div className="card">
      <div className="row space-between">
        <h3>
          Reading <ProvenanceBadge provenance="judgement" />
        </h3>
        <div className="button-row">
          {audit ? <span className="badge">{audit.status}</span> : null}
          <button
            disabled={!readable || pending || audit?.status === "running"}
            onClick={handleRun}
            title={
              readable
                ? "An LLM reads the table above and writes what was compared, what the numbers say, what differed between these runs, and why this is not a causal claim."
                : "Available once at least two runs are selected and every one of them has stopped."
            }
            type="button"
          >
            {label}
          </button>
        </div>
      </div>
      {error !== null && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}
      {audit === null ? (
        <p className="muted">
          {readable
            ? "No reading yet. Ask for one to get, in plain language, whether anything improved, which numbers were never measured, and what the differences between these runs can and cannot explain."
            : "The reading becomes available once at least two runs are selected and every one of them has stopped."}
        </p>
      ) : audit.status === "running" ? (
        <Spinner label={`Reading with ${audit.auditor_model || "the default model"}…`} />
      ) : audit.status === "failed" ? (
        <ErrorBanner message={audit.error ?? "The reading failed"} />
      ) : (
        <>
          <Markdown>{audit.markdown}</Markdown>
          <p className="muted small">
            {audit.auditor_model} ·{" "}
            {new Date(audit.updated_at).toLocaleString()} ·{" "}
            <button
              className="button-link"
              onClick={() => setShowEvidence((value) => !value)}
              type="button"
            >
              {showEvidence
                ? "hide the evidence it was given"
                : "show the evidence it was given"}
            </button>
          </p>
          {showEvidence && audit.evidence_json ? (
            <pre className="evidence-json">
              {JSON.stringify(audit.evidence_json, null, 1)}
            </pre>
          ) : null}
        </>
      )}
    </div>
  );
}
