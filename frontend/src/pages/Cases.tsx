import { useMemo, useState } from "react";

import { EmptyState } from "../components/EmptyState";
import type { CaseCreatePayload, CaseRecord } from "../types";

type CasesProps = {
  cases: CaseRecord[];
  onCreateCase: (payload: CaseCreatePayload) => Promise<CaseRecord>;
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

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function Cases({
  cases,
  onCreateCase,
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
          <span className="muted">{cases.length} total</span>
        </div>
        {cases.length === 0 ? (
          <EmptyState description="Seed Tetris or create a new case to begin." title="No cases yet." />
        ) : (
          <div className="table">
            <div className="table-row table-head">
              <span>Case</span>
              <span>Type</span>
              <span>Sync</span>
            </div>
            {cases.map((item) => (
              <button
                className={selectedCaseId === item.case_id ? "table-row selectable active" : "table-row selectable"}
                key={item.case_id}
                onClick={() => onSelectCase(item.case_id)}
                type="button"
              >
                <span>
                  <strong>{item.case_id}</strong>
                  <small>{item.name}</small>
                </span>
                <span>{item.type}</span>
                <span className="badge neutral">{item.sync_state}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
