import { useMemo, useState } from "react";

import { api } from "../api";
import { SWE_CASE_TYPES, caseTestCounts, shortInstanceLabel } from "../caseLabels";
import { EmptyState } from "../components/EmptyState";
import type { CaseCreatePayload, CaseRecord, SwebenchProInstance } from "../types";

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
        step_prompts:
          template === "multi_step"
            ? promptBody
                .split("\n")
                .map((item) => item.trim())
                .filter(Boolean)
            : [],
        success_criteria: {},
        scoring_rubric: "",
        constraints: [],
        validation_commands: [],
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
          <span className="muted">SWE-bench Lite (HuggingFace)</span>
        </div>
        <p className="muted">
          Fetches one instance from SWE-bench Lite and stores it as a case (DB only, no
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
        </div>
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
