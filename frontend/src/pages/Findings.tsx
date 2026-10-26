import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { Spinner } from "../components/Spinner";
import { IconAlert, IconCheck, IconInfo, IconX } from "../components/icons";
import { defineMessages, useMessages } from "../i18n";
import type { FindingRecord, SuggestionRecord } from "../types";

const SEVERITY_CLASS: Record<string, string> = {
  high: "badge error",
  medium: "badge",
  low: "badge neutral"
};

const MESSAGES = defineMessages({
  en: {
    statusHint: {
      suggested: "The lab noticed a pattern. Nobody has accepted it yet.",
      open: "Observed. Nothing has tested it yet.",
      proposed: "An intervention is proposed but not measured.",
      experiment_running: "An experiment is under way.",
      validated: "An experiment supported the recommendation.",
      refuted: "An experiment did not support it — kept on the record.",
      dismissed: "Set aside without testing."
    },
    severity: { low: "low", medium: "medium", high: "high" },
    status: {
      suggested: "suggested",
      open: "open",
      proposed: "proposed",
      experiment_running: "experiment running",
      validated: "validated",
      refuted: "refuted",
      dismissed: "dismissed"
    },
    queue: {
      heading: "Suggested findings",
      intro:
        "Patterns read out of the run corpus — regressions, cases that have never passed, harness items the auditor keeps judging unused. None of them is a finding until somebody accepts it.",
      yourName: "Your name",
      namePlaceholder: "Who is signing this?",
      needsName: "Accepting or rejecting needs a name on it.",
      acceptedAs: (findingId: string) =>
        `Accepted as finding '${findingId}'. It is now on the record under your name — turn it into an experiment to settle it.`,
      rejected:
        "Rejected and written down, so the same pattern will not be suggested again next week.",
      genericError: "Unable to record that",
      emptyTitle: "Nothing waiting for review.",
      emptyDescription:
        "Either the corpus shows no patterns worth raising, or every one of them has already been accepted or rejected.",
      suggestedBadge: "suggested",
      recommendationLabel: "Recommendation (edit before accepting)",
      evidence: (runs: number, cases: string, repo: string) =>
        `Evidence: ${runs} run${runs === 1 ? "" : "s"}${cases}${repo}`,
      evidenceCases: (ids: string) => ` · cases: ${ids}`,
      evidenceRepo: (repoId: string) => ` · ${repoId}`,
      accept: "Accept as finding",
      reject: "Reject"
    },
    page: {
      title: "Findings",
      lede: "What the runs showed, what to do about it, and whether that was tested.",
      helpTitle: "A finding is a claim, not a note",
      helpBody1: (
        <>
          A run is evidence. A finding is a claim about what that evidence means, and it is
          only worth making if it can be <strong>tested</strong> — which is why each one
          carries a recommendation and an intervention, and a slot for the experiment that
          settles it:
        </>
      ),
      helpPipeline: "Audit → Finding → Hypothesis → Experiment → Evidence → Recommendation",
      helpBody2: (
        <>
          A finding that cites no runs is an observation, and is labelled that way. A finding
          an experiment <em>refuted</em> keeps its record — that is the part worth not
          losing.
        </>
      ),
      tabRecord: (count: number) => `Findings of record (${count})`,
      tabSuggested: (count: number) => `Suggested (${count})`,
      genericLoadError: "Unable to load findings",
      genericSaveError: "Unable to save finding",
      genericDraftError: "Unable to draft experiment"
    },
    form: {
      heading: "Record a finding",
      idLabel: "Id",
      idPlaceholder: "migrations-edited",
      titleLabel: "Title",
      titlePlaceholder: "Agents edit legacy migration files",
      severityLabel: "Severity",
      repoLabel: "Repository",
      repoPlaceholder: "company-payments",
      evidenceLabel: "Evidence — run ids",
      evidencePlaceholder: "run-a, run-b",
      casesLabel: "Affected cases",
      casesPlaceholder: "add-endpoint",
      bodyLabel: "What was observed",
      recommendationLabel: "Recommendation — what should change",
      interventionLabel: "Intervention — the change that would test it",
      interventionPlaceholder: "migration-safety skill",
      submit: "Record finding"
    },
    table: {
      heading: "Findings",
      emptyTitle: "Nothing recorded yet",
      emptyDescription: "Findings turn a pile of runs into something a team can act on.",
      colFinding: "Finding",
      colSeverity: "Severity",
      colStatus: "Status",
      colEvidence: "Evidence",
      runsCount: (n: number) => `${n} run${n === 1 ? "" : "s"}`,
      observationOnly: "observation only",
      recommendationPrefix: "→ ",
      draftExperiment: "Draft experiment"
    },
    draft: {
      heading: "Draft experiment",
      body: (link: ReactNode) => (
        <>
          A starting point, not a created experiment: the design still needs the harness axis
          the intervention changes, and a design that varies nothing about the harness is
          refused. Review it, then build the experiment in {link}.
        </>
      ),
      newEvaluationLink: "New evaluation",
      buildInWizard: "Build it in the wizard",
      close: "Close"
    }
  },
  es: {
    statusHint: {
      suggested: "El lab notó un patrón. Todavía nadie lo aceptó.",
      open: "Observado. Nada lo probó todavía.",
      proposed: "Se propuso una intervención pero no se midió.",
      experiment_running: "Hay un experimento en curso.",
      validated: "Un experimento respaldó la recomendación.",
      refuted: "Un experimento no la respaldó — se mantiene en el registro.",
      dismissed: "Descartado sin probar."
    },
    severity: { low: "baja", medium: "media", high: "alta" },
    status: {
      suggested: "sugerido",
      open: "abierto",
      proposed: "propuesto",
      experiment_running: "experimento en curso",
      validated: "validado",
      refuted: "refutado",
      dismissed: "descartado"
    },
    queue: {
      heading: "Hallazgos sugeridos",
      intro:
        "Patrones leídos del corpus de corridas — regresiones, casos que nunca pasaron, ítems del arnés que el auditor sigue juzgando como no usados. Ninguno es un hallazgo hasta que alguien lo acepta.",
      yourName: "Tu nombre",
      namePlaceholder: "¿Quién firma esto?",
      needsName: "Para aceptar o rechazar hace falta un nombre.",
      acceptedAs: (findingId: string) =>
        `Aceptado como hallazgo '${findingId}'. Ahora está en el registro bajo tu nombre — convertilo en un experimento para resolverlo.`,
      rejected:
        "Rechazado y registrado, así que el mismo patrón no se va a sugerir de nuevo la semana que viene.",
      genericError: "No se pudo registrar eso",
      emptyTitle: "Nada esperando revisión.",
      emptyDescription:
        "O el corpus no muestra patrones que valga la pena plantear, o todos ya fueron aceptados o rechazados.",
      suggestedBadge: "sugerido",
      recommendationLabel: "Recomendación (editá antes de aceptar)",
      evidence: (runs: number, cases: string, repo: string) =>
        `Evidencia: ${runs} corrida${runs === 1 ? "" : "s"}${cases}${repo}`,
      evidenceCases: (ids: string) => ` · casos: ${ids}`,
      evidenceRepo: (repoId: string) => ` · ${repoId}`,
      accept: "Aceptar como hallazgo",
      reject: "Rechazar"
    },
    page: {
      title: "Hallazgos",
      lede: "Qué mostraron las corridas, qué hacer al respecto, y si eso se probó.",
      helpTitle: "Un hallazgo es una afirmación, no una nota",
      helpBody1: (
        <>
          Una corrida es evidencia. Un hallazgo es una afirmación sobre lo que esa evidencia
          significa, y solo vale la pena hacerla si se puede <strong>probar</strong> — por eso
          cada uno lleva una recomendación y una intervención, y un lugar para el experimento
          que lo resuelve:
        </>
      ),
      helpPipeline: "Auditoría → Hallazgo → Hipótesis → Experimento → Evidencia → Recomendación",
      helpBody2: (
        <>
          Un hallazgo que no cita ninguna corrida es una observación, y se etiqueta así. Un
          hallazgo que un experimento <em>refutó</em> mantiene su registro — eso es lo que vale
          la pena no perder.
        </>
      ),
      tabRecord: (count: number) => `Hallazgos de registro (${count})`,
      tabSuggested: (count: number) => `Sugeridos (${count})`,
      genericLoadError: "No se pudieron cargar los hallazgos",
      genericSaveError: "No se pudo guardar el hallazgo",
      genericDraftError: "No se pudo armar el borrador del experimento"
    },
    form: {
      heading: "Registrar un hallazgo",
      idLabel: "Id",
      idPlaceholder: "migrations-edited",
      titleLabel: "Título",
      titlePlaceholder: "Los agentes editan archivos de migración viejos",
      severityLabel: "Severidad",
      repoLabel: "Repositorio",
      repoPlaceholder: "company-payments",
      evidenceLabel: "Evidencia — ids de corridas",
      evidencePlaceholder: "run-a, run-b",
      casesLabel: "Casos afectados",
      casesPlaceholder: "add-endpoint",
      bodyLabel: "Qué se observó",
      recommendationLabel: "Recomendación — qué debería cambiar",
      interventionLabel: "Intervención — el cambio que lo probaría",
      interventionPlaceholder: "skill de seguridad de migraciones",
      submit: "Registrar hallazgo"
    },
    table: {
      heading: "Hallazgos",
      emptyTitle: "Todavía no hay nada registrado",
      emptyDescription:
        "Los hallazgos convierten un montón de corridas en algo sobre lo que un equipo puede actuar.",
      colFinding: "Hallazgo",
      colSeverity: "Severidad",
      colStatus: "Estado",
      colEvidence: "Evidencia",
      runsCount: (n: number) => `${n} corrida${n === 1 ? "" : "s"}`,
      observationOnly: "solo observación",
      recommendationPrefix: "→ ",
      draftExperiment: "Borrador de experimento"
    },
    draft: {
      heading: "Borrador de experimento",
      body: (link: ReactNode) => (
        <>
          Un punto de partida, no un experimento creado: el diseño todavía necesita el eje de
          arnés que cambia la intervención, y un diseño que no varía nada del arnés se rechaza.
          Revisalo y después armá el experimento en {link}.
        </>
      ),
      newEvaluationLink: "Nueva evaluación",
      buildInWizard: "Armarlo en el asistente",
      close: "Cerrar"
    }
  }
});

type FormState = {
  finding_id: string;
  title: string;
  body: string;
  severity: "low" | "medium" | "high";
  target_repo_id: string;
  evidence: string;
  cases: string;
  recommendation: string;
  intervention: string;
};

const EMPTY: FormState = {
  finding_id: "",
  title: "",
  body: "",
  severity: "medium",
  target_repo_id: "",
  evidence: "",
  cases: "",
  recommendation: "",
  intervention: ""
};

function toList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/**
 * The review queue for patterns the lab noticed (§14).
 *
 * Deliberately not a findings list. Nothing here is a claim the lab makes:
 * every row is a pattern read out of the corpus, waiting for a person to put
 * their name on it. The author field is required before Accept or Reject
 * enables, because a finding nobody owns is an assertion nobody has to defend.
 */
function SuggestionQueue({
  suggestions,
  isLoading,
  onReload,
  onError
}: {
  suggestions: SuggestionRecord[];
  isLoading: boolean;
  onReload: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const m = useMessages(MESSAGES);
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});

  const act = async (key: string, accept: boolean) => {
    setBusy(key);
    setNotice(null);
    try {
      if (accept) {
        const overrides = edits[key]?.trim()
          ? { recommendation: edits[key].trim() }
          : undefined;
        const result = await api.acceptSuggestion(key, {
          author: author.trim(),
          overrides
        });
        setNotice(m.queue.acceptedAs(result.finding.finding_id));
      } else {
        await api.rejectSuggestion(key, { author: author.trim() });
        setNotice(m.queue.rejected);
      }
      await onReload();
    } catch (error) {
      onError(error instanceof Error ? error.message : m.queue.genericError);
    } finally {
      setBusy(null);
    }
  };

  if (isLoading) {
    return (
      <div className="card">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>{m.queue.heading}</h2>
        <p className="muted">{m.queue.intro}</p>
        <label className="field">
          <span>{m.queue.yourName}</span>
          <input
            onChange={(event) => setAuthor(event.target.value)}
            placeholder={m.queue.namePlaceholder}
            value={author}
          />
        </label>
        {author.trim() ? null : (
          <p className="muted small">
            <IconInfo size={14} /> {m.queue.needsName}
          </p>
        )}
        {notice ? <p className="callout callout--ok">{notice}</p> : null}
      </div>

      {suggestions.length === 0 ? (
        <EmptyState description={m.queue.emptyDescription} title={m.queue.emptyTitle} />
      ) : (
        suggestions.map((suggestion) => (
          <article
            className={`card suggestion suggestion--${suggestion.severity}`}
            key={suggestion.key}
          >
            <div className="suggestion-head">
              <div>
                <span className="badge neutral">{m.queue.suggestedBadge}</span>{" "}
                <span className={SEVERITY_CLASS[suggestion.severity]}>
                  {m.severity[suggestion.severity]}
                </span>
                <h3>{suggestion.title}</h3>
              </div>
              <span className="muted small mono">{suggestion.source}</span>
            </div>
            <p>{suggestion.body}</p>
            <label className="field">
              <span>{m.queue.recommendationLabel}</span>
              <textarea
                onChange={(event) =>
                  setEdits({
                    ...edits,
                    [suggestion.key]: event.target.value
                  })
                }
                rows={2}
                value={edits[suggestion.key] ?? suggestion.recommendation}
              />
            </label>
            <p className="muted small">
              {m.queue.evidence(
                suggestion.evidence_run_ids.length,
                suggestion.affected_case_ids.length > 0
                  ? m.queue.evidenceCases(suggestion.affected_case_ids.join(", "))
                  : "",
                suggestion.target_repo_id ? m.queue.evidenceRepo(suggestion.target_repo_id) : ""
              )}
            </p>
            <div className="button-row">
              <button
                className="primary"
                disabled={!author.trim() || busy === suggestion.key}
                onClick={() => void act(suggestion.key, true)}
                type="button"
              >
                <IconCheck size={14} /> {m.queue.accept}
              </button>
              <button
                className="secondary"
                disabled={!author.trim() || busy === suggestion.key}
                onClick={() => void act(suggestion.key, false)}
                type="button"
              >
                <IconX size={14} /> {m.queue.reject}
              </button>
            </div>
          </article>
        ))
      )}
    </div>
  );
}

export function Findings() {
  const m = useMessages(MESSAGES);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "suggested" ? "suggested" : "record";
  const [findings, setFindings] = useState<FindingRecord[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);

  const load = async () => {
    setIsLoading(true);
    try {
      const [recorded, suggested] = await Promise.all([
        api.listFindings(),
        api.listSuggestions()
      ]);
      setFindings(recorded);
      setSuggestions(suggested.suggestions);
      setError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : m.page.genericLoadError
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleCreate = async () => {
    setError(null);
    setWarnings([]);
    try {
      const created = await api.createFinding({
        finding_id: form.finding_id.trim(),
        title: form.title.trim(),
        body: form.body,
        severity: form.severity,
        target_repo_id: form.target_repo_id.trim(),
        evidence_run_ids: toList(form.evidence),
        affected_case_ids: toList(form.cases),
        recommendation: form.recommendation,
        intervention: form.intervention
      });
      setWarnings(created.warnings ?? []);
      setForm(EMPTY);
      await load();
    } catch (createError) {
      setError(
        createError instanceof Error ? createError.message : m.page.genericSaveError
      );
    }
  };

  const handleDraft = async (findingId: string) => {
    setError(null);
    try {
      setDraft(await api.draftExperimentFromFinding(findingId));
    } catch (draftError) {
      setError(
        draftError instanceof Error ? draftError.message : m.page.genericDraftError
      );
    }
  };

  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <h1>{m.page.title}</h1>
          <p className="muted">{m.page.lede}</p>
        </div>
      </header>

      <HelpPanel doc="improve-template" title={m.page.helpTitle}>
        <p>{m.page.helpBody1}</p>
        <p className="mono small">{m.page.helpPipeline}</p>
        <p className="muted">{m.page.helpBody2}</p>
      </HelpPanel>

      {error !== null && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}
      {warnings.length > 0 && (
        <ul className="warning-list">
          {warnings.map((warning) => (
            <li key={warning}>
              <IconAlert size={14} /> {warning}
            </li>
          ))}
        </ul>
      )}

      <div className="tab-bar" role="tablist">
        <button
          aria-selected={tab === "record"}
          className={tab === "record" ? "tab tab--active" : "tab"}
          onClick={() => setParams({})}
          role="tab"
          type="button"
        >
          {m.page.tabRecord(findings.length)}
        </button>
        <button
          aria-selected={tab === "suggested"}
          className={tab === "suggested" ? "tab tab--active" : "tab"}
          onClick={() => setParams({ tab: "suggested" })}
          role="tab"
          type="button"
        >
          {m.page.tabSuggested(suggestions.length)}
        </button>
      </div>

      {tab === "suggested" ? (
        <SuggestionQueue
          isLoading={isLoading}
          onError={setError}
          onReload={load}
          suggestions={suggestions}
        />
      ) : (
        <>
          <div className="card">
            <h2>{m.form.heading}</h2>
            <div className="form-grid">
              <label>
                <span>{m.form.idLabel}</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      finding_id: event.target.value
                    })
                  }
                  placeholder={m.form.idPlaceholder}
                  value={form.finding_id}
                />
              </label>
              <label>
                <span>{m.form.titleLabel}</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      title: event.target.value
                    })
                  }
                  placeholder={m.form.titlePlaceholder}
                  value={form.title}
                />
              </label>
              <label>
                <span>{m.form.severityLabel}</span>
                <select
                  onChange={(event) =>
                    setForm({
                      ...form,
                      severity: event.target.value as FormState["severity"]
                    })
                  }
                  value={form.severity}
                >
                  <option value="low">{m.severity.low}</option>
                  <option value="medium">{m.severity.medium}</option>
                  <option value="high">{m.severity.high}</option>
                </select>
              </label>
              <label>
                <span>{m.form.repoLabel}</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      target_repo_id: event.target.value
                    })
                  }
                  placeholder={m.form.repoPlaceholder}
                  value={form.target_repo_id}
                />
              </label>
              <label>
                <span>{m.form.evidenceLabel}</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      evidence: event.target.value
                    })
                  }
                  placeholder={m.form.evidencePlaceholder}
                  value={form.evidence}
                />
              </label>
              <label>
                <span>{m.form.casesLabel}</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      cases: event.target.value
                    })
                  }
                  placeholder={m.form.casesPlaceholder}
                  value={form.cases}
                />
              </label>
            </div>
            <label className="stacked-field">
              <span>{m.form.bodyLabel}</span>
              <textarea
                onChange={(event) =>
                  setForm({
                    ...form,
                    body: event.target.value
                  })
                }
                rows={3}
                value={form.body}
              />
            </label>
            <label className="stacked-field">
              <span>{m.form.recommendationLabel}</span>
              <textarea
                onChange={(event) =>
                  setForm({
                    ...form,
                    recommendation: event.target.value
                  })
                }
                rows={2}
                value={form.recommendation}
              />
            </label>
            <label className="stacked-field">
              <span>{m.form.interventionLabel}</span>
              <input
                onChange={(event) =>
                  setForm({
                    ...form,
                    intervention: event.target.value
                  })
                }
                placeholder={m.form.interventionPlaceholder}
                value={form.intervention}
              />
            </label>
            <button
              className="primary"
              disabled={form.finding_id.trim() === "" || form.title.trim() === ""}
              onClick={() => void handleCreate()}
              type="button"
            >
              {m.form.submit}
            </button>
          </div>

          <div className="card">
            <h2>{m.table.heading}</h2>
            {isLoading ? (
              <Spinner />
            ) : findings.length === 0 ? (
              <EmptyState description={m.table.emptyDescription} title={m.table.emptyTitle} />
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{m.table.colFinding}</th>
                    <th>{m.table.colSeverity}</th>
                    <th>{m.table.colStatus}</th>
                    <th>{m.table.colEvidence}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {findings.map((finding) => (
                    <tr key={finding.finding_id}>
                      <td>
                        <strong>{finding.title}</strong>
                        <div className="muted small">
                          {finding.finding_id}
                          {finding.target_repo_id && ` · ${finding.target_repo_id}`}
                        </div>
                        {finding.recommendation && (
                          <div className="small">
                            {m.table.recommendationPrefix}
                            {finding.recommendation}
                          </div>
                        )}
                      </td>
                      <td>
                        <span className={SEVERITY_CLASS[finding.severity]}>
                          {m.severity[finding.severity]}
                        </span>
                      </td>
                      <td className="small">
                        {m.status[finding.status]}
                        <div className="muted small">{m.statusHint[finding.status]}</div>
                        {finding.experiment_id && (
                          <div className="mono small">{finding.experiment_id}</div>
                        )}
                      </td>
                      <td className="small">
                        {finding.evidence_run_ids.length > 0 ? (
                          m.table.runsCount(finding.evidence_run_ids.length)
                        ) : (
                          <span className="muted">{m.table.observationOnly}</span>
                        )}
                      </td>
                      <td>
                        <button
                          onClick={() => void handleDraft(finding.finding_id)}
                          type="button"
                        >
                          {m.table.draftExperiment}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {draft !== null && (
            <div className="card">
              <h2>{m.draft.heading}</h2>
              <p className="muted">
                {m.draft.body(
                  <Link to="/evaluate/new">{m.draft.newEvaluationLink}</Link>
                )}
              </p>
              <pre className="mono small">{JSON.stringify(draft, null, 2)}</pre>
              <div className="button-row">
                <Link className="primary" to="/evaluate/new">
                  {m.draft.buildInWizard}
                </Link>
                <button onClick={() => setDraft(null)} type="button">
                  {m.draft.close}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
