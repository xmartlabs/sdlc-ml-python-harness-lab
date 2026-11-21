import { useEffect, useState } from "react";

import { api } from "../api";
import { ErrorBanner } from "./ErrorBanner";
import { HarnessCatalog } from "./HarnessCatalog";
import { Markdown } from "./Markdown";
import { SandboxPanel } from "./SandboxPanel";
import { defineMessages, useMessages } from "../i18n";
import type { AdhocRunRecord, AttributionRecord, AuditRecord, RunMetadata } from "../types";

/**
 * What a benchmark run offers, for an ad hoc run (ADR-0095): open its
 * workspace in a terminal or desktop, grade it with the LLM judge against a
 * generic "did it do what was asked" rubric, and read the LLM audit and the
 * skills the agent opened. The case's own checks do not apply — the task is a
 * free prompt — so the judge's rubric is the task itself.
 */

type Tab = "evaluate" | "terminal" | "desktop";

const MESSAGES = defineMessages({
  en: {
    tabs: { evaluate: "Evaluate", terminal: "Terminal", desktop: "Desktop" } as Record<Tab, string>,
    sandboxHint:
      "A copy of the run's workspace: run the tests, start the app, play the game. Nothing here touches your folder.",
    noWorkspace: "This run's workspace was purged, so there is nothing to open. The metrics stay.",
    judgeTitle: "LLM judge",
    judgeHint:
      "Grades the change 0-10 against the task itself: does it do what was asked, without unrelated edits. One model call.",
    judge: "Grade with the LLM judge",
    judging: "Grading…",
    judgeScore: (score: number) => `${score}/10`,
    judgeAgain: "Grade again",
    judgeFailed: "The judge did not answer. Check the run's events, or try again.",
    auditTitle: "Skills used and LLM audit",
    auditHint:
      "The audit reads the run's steps, diff and prompt and explains how the agent worked and which skills it opened.",
    runAudit: "Run the LLM audit",
    auditing: "Auditing…",
    auditFailed: "The audit failed.",
    noSkills: "No skills recorded for this run yet."
  },
  es: {
    tabs: { evaluate: "Evaluar", terminal: "Terminal", desktop: "Escritorio" } as Record<Tab, string>,
    sandboxHint:
      "Una copia del workspace de la corrida: corré los tests, levantá la app, jugá el juego. Nada de esto toca tu carpeta.",
    noWorkspace: "El workspace de esta corrida se purgó, así que no hay nada que abrir. Las métricas quedan.",
    judgeTitle: "Juez LLM",
    judgeHint:
      "Puntúa el cambio de 0 a 10 contra la tarea misma: si hace lo que se pidió, sin cambios ajenos. Una llamada al modelo.",
    judge: "Puntuar con el juez LLM",
    judging: "Puntuando…",
    judgeScore: (score: number) => `${score}/10`,
    judgeAgain: "Puntuar de nuevo",
    judgeFailed: "El juez no respondió. Revisá los eventos de la corrida o probá de nuevo.",
    auditTitle: "Skills usadas y auditoría LLM",
    auditHint:
      "La auditoría lee los pasos, el diff y el prompt de la corrida, y explica cómo trabajó el agente y qué skills abrió.",
    runAudit: "Correr la auditoría LLM",
    auditing: "Auditando…",
    auditFailed: "La auditoría falló.",
    noSkills: "Todavía no hay skills registradas para esta corrida."
  }
});

const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

export function AdhocRunEvaluation({
  run,
  onChanged
}: {
  run: AdhocRunRecord;
  onChanged: (updated: AdhocRunRecord) => void;
}) {
  const m = useMessages(MESSAGES);
  const [tab, setTab] = useState<Tab>("evaluate");
  const [error, setError] = useState<string | null>(null);
  const [judging, setJudging] = useState(false);
  const [audit, setAudit] = useState<AuditRecord | null>(null);
  const [attribution, setAttribution] = useState<AttributionRecord | null>(null);
  const [metadata, setMetadata] = useState<RunMetadata | null>(null);

  useEffect(() => {
    let active = true;
    setAudit(null);
    setAttribution(null);
    setMetadata(null);
    api
      .getAudit(run.run_id)
      .then((result) => active && setAudit(result))
      .catch(() => {
        // No audit yet (404) until one is requested.
      });
    api
      .getAttribution(run.run_id)
      .then((result) => active && setAttribution(result))
      .catch(() => {
        // Indexed after the run; absent for a run that left no steps.
      });
    api
      .readArtifact(run.run_id, "metadata.json")
      .then((result) => {
        if (!active) return;
        try {
          setMetadata(JSON.parse(result.content) as RunMetadata);
        } catch {
          setMetadata(null);
        }
      })
      .catch(() => {
        // Purged runs have no metadata file.
      });
    return () => {
      active = false;
    };
  }, [run.run_id]);

  const judge = async () => {
    setJudging(true);
    setError(null);
    try {
      let latest = await api.judgeAdhocRun(run.run_id);
      onChanged(latest);
      // One model call in the worker: poll until a score lands or it gives up.
      for (let tries = 0; tries < 40 && latest.judge_score == null; tries += 1) {
        await sleep(3000);
        latest = await api.getAdhocRun(run.run_id);
        onChanged(latest);
      }
      if (latest.judge_score == null) setError(m.judgeFailed);
    } catch (judgeError) {
      setError(judgeError instanceof Error ? judgeError.message : String(judgeError));
    } finally {
      setJudging(false);
    }
  };

  const runAudit = async () => {
    setError(null);
    setAudit({
      run_id: run.run_id,
      auditor_model: "",
      status: "running",
      markdown: "",
      error: null,
      created_at: "",
      updated_at: ""
    });
    try {
      await api.runAudit(run.run_id);
      for (let tries = 0; tries < 60; tries += 1) {
        await sleep(3000);
        const result = await api.getAudit(run.run_id).catch(() => null);
        if (result) setAudit(result);
        if (result && result.status !== "running") break;
      }
      const refreshed = await api.getAttribution(run.run_id).catch(() => null);
      if (refreshed) setAttribution(refreshed);
    } catch (auditError) {
      setError(auditError instanceof Error ? auditError.message : String(auditError));
    }
  };

  const tabs: Tab[] = ["evaluate", "terminal", "desktop"];

  return (
    <div className="card adhoc-evaluation">
      <div className="run-tabs">
        {tabs.map((name) => (
          <button
            className={tab === name ? "run-tab active" : "run-tab"}
            key={name}
            onClick={() => setTab(name)}
            type="button"
          >
            {m.tabs[name]}
          </button>
        ))}
      </div>
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {tab !== "evaluate" ? (
        run.has_workspace ? (
          <>
            <p className="muted small">{m.sandboxHint}</p>
            <SandboxPanel key={tab} kind={tab} runId={run.run_id} />
          </>
        ) : (
          <p className="muted small">{m.noWorkspace}</p>
        )
      ) : (
        <>
          <section>
            <h3>{m.judgeTitle}</h3>
            <p className="muted small">{m.judgeHint}</p>
            {run.judge_score != null ? (
              <p>
                <span className="badge ok">{m.judgeScore(run.judge_score)}</span>{" "}
                <span className="small">{(run.judge_notes ?? "").replace(/^LLM-judged[^:]*:\s*/, "")}</span>
              </p>
            ) : null}
            <button
              className="ghost"
              disabled={judging || run.files_purged}
              onClick={() => void judge()}
              type="button"
            >
              {judging ? m.judging : run.judge_score != null ? m.judgeAgain : m.judge}
            </button>
          </section>

          <section>
            <h3>{m.auditTitle}</h3>
            <p className="muted small">{m.auditHint}</p>
            <button
              className="ghost"
              disabled={audit?.status === "running" || run.files_purged}
              onClick={() => void runAudit()}
              type="button"
            >
              {audit?.status === "running" ? m.auditing : m.runAudit}
            </button>
            {audit?.status === "failed" ? <ErrorBanner message={audit.error ?? m.auditFailed} /> : null}
            {attribution || metadata || audit ? (
              <HarnessCatalog attribution={attribution} audit={audit} metadata={metadata} />
            ) : (
              <p className="muted small">{m.noSkills}</p>
            )}
            {audit?.status === "done" && audit.markdown ? (
              <div className="audit-body">
                <Markdown>{audit.markdown}</Markdown>
              </div>
            ) : null}
          </section>
        </>
      )}
    </div>
  );
}
