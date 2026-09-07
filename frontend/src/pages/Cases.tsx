import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { SWE_CASE_TYPES, caseTestCounts, shortInstanceLabel } from "../caseLabels";
import { EmptyState } from "../components/EmptyState";
import { Spinner } from "../components/Spinner";
import { IconAlert, IconSparkles } from "../components/icons";
import type {
  CaseCreatePayload,
  CaseRecord,
  DroppedCommand,
  ModelOption,
  SwebenchProInstance
} from "../types";

type CasesProps = {
  cases: CaseRecord[];
  onCreateCase: (payload: CaseCreatePayload) => Promise<CaseRecord>;
  onImportSwebench: (instanceId: string) => Promise<CaseRecord>;
  onImportSwebenchPro: (instanceId: string) => Promise<CaseRecord>;
  onSeedTetris: () => void;
  onSelectCase: (caseId: string) => void;
  selectedCaseId: string | null;
};

const CASE_TEMPLATES = [
  { value: "single_prompt", label: "Single prompt" },
  { value: "multi_step", label: "Multi-step" },
  { value: "build_from_scratch", label: "Build from scratch" },
  { value: "bugfix", label: "Bugfix" },
  { value: "repo_task", label: "Repo task" }
] as const;

type CaseFilter = "all" | "swe-bench" | "other";

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function caseTypeBadge(item: CaseRecord) {
  if (SWE_CASE_TYPES.has(item.type)) {
    const tests = caseTestCounts(item);
    const counts = tests ? ` · F2P ${tests.f2p} / P2P ${tests.p2p}` : "";
    const label = item.type === "swe_pro_instance" ? "SWE-bench Pro" : "SWE-bench";
    return <span className="badge ok">{label}{counts}</span>;
  }
  return <span>{item.type}</span>;
}

export function Cases({
  cases,
  onCreateCase,
  onImportSwebench,
  onImportSwebenchPro,
  onSeedTetris,
  onSelectCase,
  selectedCaseId
}: CasesProps) {
  const [template, setTemplate] = useState("single_prompt");
  const [name, setName] = useState("New case");
  const [caseId, setCaseId] = useState("new-case");
  const [description, setDescription] = useState("");
  const [promptBody, setPromptBody] = useState("Describe the task here.");
  const [maxSteps, setMaxSteps] = useState(1);
  // One command per line, the shape the case stores and the runner executes.
  const [commandText, setCommandText] = useState("");
  const [stepPrompts, setStepPrompts] = useState<string[]>([]);
  const [aiDescription, setAiDescription] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiNotice, setAiNotice] = useState("");
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiDropped, setAiDropped] = useState<DroppedCommand[]>([]);
  const [isDrafting, setIsDrafting] = useState(false);
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [swebenchId, setSwebenchId] = useState("pallets__flask-4992");
  const [isImporting, setIsImporting] = useState(false);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [caseFilter, setCaseFilter] = useState<CaseFilter>("all");
  const [proCatalog, setProCatalog] = useState<SwebenchProInstance[] | null>(null);
  const [isLoadingCatalog, setIsLoadingCatalog] = useState(false);
  const [proRepoFilter, setProRepoFilter] = useState<string>("all");
  const [proImportingId, setProImportingId] = useState<string | null>(null);
  const [proMessage, setProMessage] = useState<string | null>(null);
  const [proError, setProError] = useState<string | null>(null);
  const [proExpandedId, setProExpandedId] = useState<string | null>(null);
  // The assistant's model menu. A catalog the lab cannot reach is not an
  // error here: the request falls back to the transport's own default model.
  useEffect(() => {
    let active = true;
    api
      .listModels(false)
      .then((catalog) => {
        if (!active) return;
        setModelOptions(catalog.models);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const templateLabel = useMemo(
    () => CASE_TEMPLATES.find((item) => item.value === template)?.label ?? template,
    [template]
  );

  const handleTemplateChange = (nextTemplate: string) => {
    setTemplate(nextTemplate);
    if (!caseId || caseId === slugify(name)) {
      setCaseId(`${nextTemplate}-case`);
    }
    if (nextTemplate === "multi_step") {
      setPromptBody("Step 1: Explain the task.\nStep 2: Implement the fix.\nStep 3: Verify the result.");
      setMaxSteps(3);
    } else {
      setPromptBody("Describe the task here.");
      setMaxSteps(1);
    }
  };

  const handleNameChange = (value: string) => {
    setName(value);
    if (caseId === "new-case" || caseId === slugify(name)) {
      setCaseId(slugify(value) || "new-case");
    }
  };

  const handleImportSwebench = async () => {
    setIsImporting(true);
    setImportMessage(null);
    setImportError(null);
    try {
      const imported = await onImportSwebench(swebenchId.trim());
      setImportMessage(`Imported ${imported.case_id} — ready to run.`);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsImporting(false);
    }
  };

  const visibleCases = useMemo(() => {
    if (caseFilter === "swe-bench") {
      return cases.filter((item) => SWE_CASE_TYPES.has(item.type));
    }
    if (caseFilter === "other") {
      return cases.filter((item) => !SWE_CASE_TYPES.has(item.type));
    }
    return cases;
  }, [cases, caseFilter]);

  const handleLoadProCatalog = async () => {
    setIsLoadingCatalog(true);
    setProError(null);
    try {
      setProCatalog(await api.listSwebenchProInstances());
    } catch (error) {
      setProError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoadingCatalog(false);
    }
  };

  const handleImportPro = async (instanceId: string) => {
    setProImportingId(instanceId);
    setProMessage(null);
    setProError(null);
    try {
      const imported = await onImportSwebenchPro(instanceId);
      setProMessage(`Imported ${shortInstanceLabel(imported.case_id)} — ready to run.`);
      setProCatalog((current) =>
        current
          ? current.map((item) =>
              item.instance_id === instanceId ? { ...item, imported: true } : item
            )
          : current
      );
    } catch (error) {
      setProError(error instanceof Error ? error.message : String(error));
    } finally {
      setProImportingId(null);
    }
  };

  const proRepos = useMemo(() => {
    const repos = new Set((proCatalog ?? []).map((item) => item.repo));
    return ["all", ...Array.from(repos).sort()];
  }, [proCatalog]);

  const visibleProInstances = useMemo(() => {
    const items = proCatalog ?? [];
    return proRepoFilter === "all"
      ? items
      : items.filter((item) => item.repo === proRepoFilter);
  }, [proCatalog, proRepoFilter]);

  /**
   * Fill the form from one sentence.
   *
   * The assistant returns the whole case — type, id, name, description,
   * instruction, per-step prompts and validation commands — because this form
   * saves all of it in one press. Nothing is created here: every field lands
   * editable, so the draft is a starting point and not a decision.
   */
  const handleGenerate = async () => {
    setIsDrafting(true);
    setAiError(null);
    setAiNotice("");
    setAiDropped([]);
    try {
      const draft = await api.generateCaseDraft({
        description: aiDescription,
        model: aiModel || undefined,
        selected_skills: [],
        existing_case_ids: cases.map((item) => item.case_id)
      });
      const draftType = draft.case_type || "single_prompt";
      setTemplate(draftType);
      if (draft.case_id) setCaseId(draft.case_id);
      if (draft.subject) setName(draft.subject);
      if (draft.description) setDescription(draft.description);
      if (draft.instruction) setPromptBody(draft.instruction);
      setStepPrompts(draft.step_prompts ?? []);
      // The server screened these: what is left is one runnable command per
      // line, which is the only shape this textarea preserves.
      setCommandText((draft.validation_commands ?? []).join("\n"));
      if (draft.max_steps) setMaxSteps(draft.max_steps);
      setAiDropped(draft.dropped_commands ?? []);

      const kept = draft.validation_commands?.length ?? 0;
      const retried = draft.attempts > 1 ? " after one corrective retry" : "";
      const plan =
        draftType === "multi_step" && (draft.step_prompts?.length ?? 0) > 0
          ? `, a ${1 + (draft.step_prompts?.length ?? 0)}-step plan`
          : "";
      if (kept === 0) {
        setAiError(
          `The draft has no runnable validation command${retried}. Without one the ` +
            "case cannot score itself and every run waits for a manual score — " +
            "write one below, or describe the check you want and generate again."
        );
      } else {
        setAiNotice(
          `Drafted a ${draftType.replace(/_/g, " ")} case${plan} with ${kept} ` +
            `validation command(s)${retried}. Review every field, then Create case.`
        );
      }
    } catch (error) {
      setAiError(
        error instanceof Error ? error.message : "Unable to generate the case draft."
      );
    } finally {
      setIsDrafting(false);
    }
  };

  const handleCreate = async () => {
    setIsSaving(true);
    try {
      const created = await onCreateCase({
        case_id: caseId || slugify(name) || "new-case",
        name: name || "New case",
        type: template,
        description,
        tags: ["ui"],
        prompt_variants: { default: promptBody },
        // A drafted plan drives step 2 onward; typing the steps into the
        // prompt body keeps the old behaviour for a hand-written case.
        step_prompts:
          template === "multi_step"
            ? stepPrompts.length > 0
              ? stepPrompts
              : promptBody
                  .split("\n")
                  .map((item) => item.trim())
                  .filter(Boolean)
            : [],
        success_criteria: {},
        scoring_rubric: "",
        constraints: [],
        validation_commands: commandText
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        max_steps: maxSteps
      });
      onSelectCase(created.case_id);
      setCaseId(`${slugify(created.name)}-copy`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Cases</p>
          <h1>Benchmark cases</h1>
          <p className="lede">Create, inspect, and sync case definitions from one place.</p>
        </div>
        <div className="toolbar">
          <button className="primary" onClick={onSeedTetris} type="button">
            Seed Tetris
          </button>
        </div>
      </header>

      <section className="panel">
        <div className="panel-head">
          <h2>Import SWE-bench instance</h2>
          <span className="muted">SWE-bench Lite / Verified (HuggingFace)</span>
        </div>
        <p className="muted">
          Fetches one instance from SWE-bench Lite / Verified and stores it as a case (DB only, no
          YAML on disk). Equivalent CLI: <code>make swebench-import INSTANCE=&lt;id&gt;</code>.
          Prove the harness first with <code>make swebench-selftest INSTANCE=&lt;id&gt;</code>.
        </p>
        <div className="form-grid">
          <label>
            <span>Instance ID</span>
            <input
              placeholder="pallets__flask-4992"
              value={swebenchId}
              onChange={(event) => setSwebenchId(event.target.value)}
            />
          </label>
        </div>
        <div className="panel-actions">
          <button
            className="primary"
            disabled={isImporting || swebenchId.trim().length === 0}
            onClick={handleImportSwebench}
            type="button"
          >
            {isImporting ? "Importing..." : "Import instance"}
          </button>
          {importMessage ? <span className="badge ok">{importMessage}</span> : null}
          {importError ? <span className="badge error">{importError}</span> : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Import SWE-bench Pro instance</h2>
          <span className="muted">Hard cases · Python subset (ScaleAI)</span>
        </div>
        <p className="muted">
          Long-horizon, multi-file tasks evaluated inside prebuilt per-instance Docker
          images. Browse the catalog and import — no need to know the instance id.
          Equivalent CLI: <code>make swebench-pro-import INSTANCE=&lt;id&gt;</code>; prove the
          lane first with <code>make swebench-pro-selftest INSTANCE=&lt;id&gt;</code>.
        </p>
        {proCatalog === null ? (
          <div className="panel-actions">
            <button
              className="primary"
              disabled={isLoadingCatalog}
              onClick={handleLoadProCatalog}
              type="button"
            >
              {isLoadingCatalog ? "Loading catalog..." : "Browse instances"}
            </button>
            {proError ? <span className="badge error">{proError}</span> : null}
          </div>
        ) : (
          <>
            <div className="toolbar" style={{ marginBottom: "0.75rem" }}>
              {proRepos.map((repo) => (
                <button
                  className={proRepoFilter === repo ? "badge neutral" : "badge"}
                  key={repo}
                  onClick={() => setProRepoFilter(repo)}
                  type="button"
                >
                  {repo === "all"
                    ? `All (${(proCatalog ?? []).length})`
                    : `${repo} (${(proCatalog ?? []).filter((item) => item.repo === repo).length})`}
                </button>
              ))}
            </div>
            <div className="panel-actions" style={{ marginBottom: "0.5rem" }}>
              {proMessage ? <span className="badge ok">{proMessage}</span> : null}
              {proError ? <span className="badge error">{proError}</span> : null}
            </div>
            <div className="table" style={{ maxHeight: "28rem", overflowY: "auto" }}>
              <div className="table-row table-head">
                <span>Instance</span>
                <span>Tests</span>
                <span></span>
              </div>
              {visibleProInstances.map((item) => {
                const expanded = proExpandedId === item.instance_id;
                return (
                  <div className="table-row" key={item.instance_id}>
                    <span
                      onClick={() =>
                        setProExpandedId(expanded ? null : item.instance_id)
                      }
                      style={{ cursor: "pointer" }}
                      title={item.instance_id}
                    >
                      <strong style={{ display: "block" }}>
                        {item.title || shortInstanceLabel(item.instance_id)}
                      </strong>
                      <small className="muted" style={{ display: "block" }}>
                        {shortInstanceLabel(item.instance_id)}
                        {item.summary && !expanded
                          ? ` — ${item.summary.slice(0, 110)}…`
                          : ""}
                      </small>
                      {expanded ? (
                        <span style={{ display: "block", marginTop: "0.35rem" }}>
                          {item.summary ? (
                            <small style={{ display: "block", whiteSpace: "normal" }}>
                              {item.summary}
                            </small>
                          ) : null}
                          {item.issue_categories.length > 0 ? (
                            <span
                              style={{
                                display: "flex",
                                flexWrap: "wrap",
                                gap: "0.3rem",
                                marginTop: "0.35rem"
                              }}
                            >
                              {item.issue_categories.map((category) => (
                                <span className="badge neutral" key={category}>
                                  {category.replaceAll("_", " ")}
                                </span>
                              ))}
                            </span>
                          ) : null}
                        </span>
                      ) : null}
                    </span>
                    <span className="muted">
                      F2P {item.f2p_count} / P2P {item.p2p_count}
                    </span>
                    <span>
                      {item.imported ? (
                        <span className="badge ok">Imported</span>
                      ) : (
                        <button
                          className="badge"
                          disabled={proImportingId !== null}
                          onClick={() => handleImportPro(item.instance_id)}
                          type="button"
                        >
                          {proImportingId === item.instance_id
                            ? "Importing..."
                            : "Import"}
                        </button>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>New case</h2>
          <span className="muted">{templateLabel}</span>
        </div>

        {/* AI case assistant: optional, fills every field below. */}
        <div className="assist-card">
          <div className="assist-card-head">
            <strong className="assist-card-title">
              <IconSparkles size={16} /> Draft it with AI (optional)
            </strong>
            <span className="muted small">
              Shapes the whole case — type, id, instruction, steps and validation
            </span>
          </div>

          <label>
            <span>Describe the case you want, in plain language</span>
            <textarea
              onChange={(event) => setAiDescription(event.target.value)}
              placeholder="e.g. A CLI that reads a CSV of transactions and writes a monthly summary as JSON, with tests for empty files and malformed rows."
              rows={2}
              value={aiDescription}
            />
          </label>

          <div className="assist-card-row">
            <label>
              {/* Empty means "let the transport pick": the gateway's summary-tier
                  model, or Claude Haiku on a subscription lab. The catalog's
                  default is an *executor* model, which drafts prose worse than
                  it writes code, so it is offered rather than preselected. */}
              <span>Model</span>
              <select
                onChange={(event) => setAiModel(event.target.value)}
                value={aiModel}
              >
                <option value="">Transport default (recommended)</option>
                {modelOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label || option.id}
                  </option>
                ))}
              </select>
            </label>

            <button
              className="primary"
              disabled={isDrafting || !aiDescription.trim()}
              onClick={() => void handleGenerate()}
              type="button"
            >
              {isDrafting ? (
                <Spinner label="Drafting…" />
              ) : (
                <>
                  <IconSparkles size={14} /> Generate the case
                </>
              )}
            </button>
          </div>

          {aiNotice ? <p className="callout callout--ok">{aiNotice}</p> : null}
          {aiError ? (
            <div className="callout callout--warning">
              <IconAlert size={16} />
              <span>{aiError}</span>
            </div>
          ) : null}
          {aiDropped.length > 0 ? (
            <div className="callout callout--warning">
              <IconAlert size={16} />
              <div>
                <strong>Left out of the draft — the lab would not run these:</strong>
                <ul className="blocking-list dropped-list">
                  {aiDropped.map((item) => (
                    <li key={item.command}>
                      <code>{item.command.split("\n")[0]}</code>
                      {item.command.includes("\n") ? " …" : ""}
                      <span className="muted small"> — {item.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}
        </div>

        <div className="form-grid">
          <label>
            <span>Template</span>
            <select value={template} onChange={(event) => handleTemplateChange(event.target.value)}>
              {CASE_TEMPLATES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Case ID</span>
            <input value={caseId} onChange={(event) => setCaseId(event.target.value)} />
          </label>
          <label>
            <span>Name</span>
            <input value={name} onChange={(event) => handleNameChange(event.target.value)} />
          </label>
          <label>
            <span>Max steps</span>
            <input
              min={1}
              type="number"
              value={maxSteps}
              onChange={(event) => setMaxSteps(Number(event.target.value))}
            />
          </label>
          <label className="full">
            <span>Description</span>
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} />
          </label>
          <label className="full">
            <span>Prompt body</span>
            <textarea value={promptBody} onChange={(event) => setPromptBody(event.target.value)} />
          </label>
          {template === "multi_step" && stepPrompts.length > 0 ? (
            <label className="full">
              <span>Step prompts — one per line, driving step 2 onward</span>
              <textarea
                onChange={(event) =>
                  setStepPrompts(
                    event.target.value.split("\n").map((item) => item.trim())
                  )
                }
                rows={Math.min(stepPrompts.length + 1, 8)}
                value={stepPrompts.join("\n")}
              />
            </label>
          ) : null}
          <label className="full">
            <span>Validation commands — one per line</span>
            <textarea
              onChange={(event) => setCommandText(event.target.value)}
              placeholder="pytest {workspace}/test_summary.py -q"
              value={commandText}
            />
          </label>
        </div>
        <p className="muted small">
          A case with no validation command cannot score itself: every run of it
          stops in <code>awaiting_manual_score</code> until a person grades it.
        </p>
        <div className="panel-actions">
          <button className="primary" disabled={isSaving} onClick={handleCreate} type="button">
            {isSaving ? "Saving..." : "Create case"}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Case list</h2>
          <div className="toolbar">
            {(
              [
                { value: "all", label: `All (${cases.length})` },
                {
                  value: "swe-bench",
                  label: `SWE-bench (${cases.filter((item) => SWE_CASE_TYPES.has(item.type)).length})`
                },
                {
                  value: "other",
                  label: `Other (${cases.filter((item) => !SWE_CASE_TYPES.has(item.type)).length})`
                }
              ] as { value: CaseFilter; label: string }[]
            ).map((option) => (
              <button
                className={caseFilter === option.value ? "badge neutral" : "badge"}
                key={option.value}
                onClick={() => setCaseFilter(option.value)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        {visibleCases.length === 0 ? (
          <EmptyState
            description={
              caseFilter === "swe-bench"
                ? "No SWE-bench cases yet. Import one above (they live in the DB only, so a DB reset removes them)."
                : "Seed Tetris or create a new case to begin."
            }
            title="No cases here."
          />
        ) : (
          <div className="table">
            <div className="table-row table-head">
              <span>Case</span>
              <span>Type</span>
              <span>Sync</span>
            </div>
            {visibleCases.map((item) => (
              <button
                className={selectedCaseId === item.case_id ? "table-row selectable active" : "table-row selectable"}
                key={item.case_id}
                onClick={() => onSelectCase(item.case_id)}
                type="button"
              >
                <span>
                  <strong style={{ display: "block" }}>
                    {item.name && item.name !== item.case_id
                      ? item.name
                      : item.case_id}
                  </strong>
                  <small className="muted" style={{ display: "block" }}>
                    {SWE_CASE_TYPES.has(item.type)
                      ? shortInstanceLabel(item.case_id)
                      : item.case_id}
                  </small>
                </span>
                {caseTypeBadge(item)}
                <span className="badge neutral">{item.sync_state}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
