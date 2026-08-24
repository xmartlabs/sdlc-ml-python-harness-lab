import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { SWE_CASE_TYPES } from "../caseLabels";
import type { CaseCreatePayload, CaseRecord } from "../types";

type CaseEditorProps = {
  caseRecord: CaseRecord;
  onSaved: (updated: CaseRecord) => void;
  onCloned: (created: CaseRecord) => void;
  onDeleted: (caseId: string) => void;
};

const CASE_TYPES = [
  "single_prompt",
  "multi_step",
  "build_from_scratch",
  "bugfix",
  "repo_task"
] as const;

type PromptVariant = { name: string; body: string };
type CriterionRow = { key: string; value: string };

/** The editable shape of a case, decoupled from the API payload's ordering. */
type Draft = {
  name: string;
  description: string;
  type: string;
  maxSteps: number;
  variants: PromptVariant[];
  stepPrompts: string[];
  criteria: CriterionRow[];
  scoringRubric: string;
  constraints: string;
  validationCommands: string;
};

function toDraft(item: CaseRecord): Draft {
  return {
    name: item.name ?? "",
    description: item.description ?? "",
    type: item.type,
    maxSteps: item.max_steps ?? 1,
    variants: Object.entries(item.prompt_variants ?? {}).map(([name, body]) => ({
      name,
      body
    })),
    stepPrompts: [...(item.step_prompts ?? [])],
    criteria: Object.entries(item.success_criteria ?? {}).map(([key, value]) => ({
      key,
      value
    })),
    scoringRubric: item.scoring_rubric ?? "",
    constraints: (item.constraints ?? []).join("\n"),
    validationCommands: (item.validation_commands ?? []).join("\n")
  };
}

function toLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function toPayload(caseId: string, draft: Draft): CaseCreatePayload {
  const promptVariants: Record<string, string> = {};
  for (const variant of draft.variants) {
    const name = variant.name.trim();
    if (name) {
      promptVariants[name] = variant.body;
    }
  }
  const successCriteria: Record<string, string> = {};
  for (const row of draft.criteria) {
    const key = row.key.trim();
    if (key) {
      successCriteria[key] = row.value;
    }
  }
  return {
    case_id: caseId,
    name: draft.name.trim() || caseId,
    type: draft.type,
    description: draft.description,
    tags: [],
    prompt_variants: promptVariants,
    step_prompts: draft.stepPrompts.map((step) => step.trim()).filter(Boolean),
    success_criteria: successCriteria,
    scoring_rubric: draft.scoringRubric,
    constraints: toLines(draft.constraints),
    validation_commands: toLines(draft.validationCommands),
    max_steps: Number.isFinite(draft.maxSteps) ? draft.maxSteps : 1
  };
}

export function CaseEditor({
  caseRecord,
  onSaved,
  onCloned,
  onDeleted
}: CaseEditorProps) {
  const caseId = caseRecord.case_id;
  const [draft, setDraft] = useState<Draft>(() => toDraft(caseRecord));
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // SWE-bench cases mirror an imported dataset: their prompts and test lists
  // define the benchmark, so editing them would silently invalidate every
  // comparison already recorded against the case.
  const readOnly = SWE_CASE_TYPES.has(caseRecord.type);

  useEffect(() => {
    let cancelled = false;
    setDraft(toDraft(caseRecord));
    setMessage(null);
    setError(null);
    // The list endpoint is enough to render, but re-reading the case makes the
    // editor authoritative when another tab (or the CLI) changed it meanwhile.
    api
      .getCase(caseId)
      .then((fresh) => {
        if (!cancelled) {
          setDraft(toDraft(fresh));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // caseRecord is re-created by the parent on every list refresh; keying on
    // the id is what actually means "a different case is selected".
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  const withBusy = useCallback(
    async (action: string, work: () => Promise<string>) => {
      setBusy(action);
      setMessage(null);
      setError(null);
      try {
        setMessage(await work());
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setBusy(null);
      }
    },
    []
  );

  const handleSave = () =>
    withBusy("save", async () => {
      const updated = await api.updateCase(caseId, toPayload(caseId, draft));
      onSaved(updated);
      return "Saved.";
    });

  const handleSync = () =>
    withBusy("sync", async () => {
      const result = await api.syncCase(caseId);
      const fresh = await api.getCase(caseId);
      onSaved(fresh);
      return `Exported to ${result.task_path}`;
    });

  const handleClone = () =>
    withBusy("clone", async () => {
      const newCaseId = window.prompt("New case id", `${caseId}-copy`);
      if (!newCaseId) {
        return "Clone cancelled.";
      }
      const created = await api.cloneCase(
        caseId,
        newCaseId.trim(),
        `${draft.name || caseId} (copy)`
      );
      onCloned(created);
      return `Cloned as ${created.case_id}.`;
    });

  const handleDelete = () =>
    withBusy("delete", async () => {
      if (!window.confirm(`Delete case ${caseId}? Its runs are kept.`)) {
        return "Delete cancelled.";
      }
      await api.deleteCase(caseId);
      onDeleted(caseId);
      return `Deleted ${caseId}.`;
    });

  const patch = (changes: Partial<Draft>) =>
    setDraft((current) => ({ ...current, ...changes }));

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Edit case</h2>
        <span className="badge neutral">{caseRecord.sync_state}</span>
      </div>
      {readOnly ? (
        <p className="muted">
          This case was imported from the SWE-bench dataset, so its definition is
          read-only — editing the prompts or test lists would invalidate the
          benchmark. Clone it to build a variant you can change.
        </p>
      ) : null}

      <div className="form-grid">
        <label>
          <span>Case ID</span>
          <input readOnly value={caseId} />
          <small className="muted">
            Fixed: it is the key every run in the history joins on.
          </small>
        </label>
        <label>
          <span>Name</span>
          <input
            disabled={readOnly}
            onChange={(event) => patch({ name: event.target.value })}
            value={draft.name}
          />
        </label>
        <label>
          <span>Type</span>
          <select
            disabled={readOnly}
            onChange={(event) => patch({ type: event.target.value })}
            value={draft.type}
          >
            {(CASE_TYPES as readonly string[]).includes(draft.type) ? null : (
              <option value={draft.type}>{draft.type}</option>
            )}
            {CASE_TYPES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Max steps</span>
          <input
            disabled={readOnly}
            min={1}
            onChange={(event) =>
              patch({ maxSteps: Number.parseInt(event.target.value, 10) || 1 })
            }
            type="number"
            value={draft.maxSteps}
          />
        </label>
      </div>

      <label>
        <span>Description</span>
        <textarea
          disabled={readOnly}
          onChange={(event) => patch({ description: event.target.value })}
          rows={2}
          value={draft.description}
        />
      </label>

      <div className="panel-head">
        <h3>Prompt variants</h3>
        {readOnly ? null : (
          <button
            className="badge"
            onClick={() =>
              patch({ variants: [...draft.variants, { name: "", body: "" }] })
            }
            type="button"
          >
            Add variant
          </button>
        )}
      </div>
      <p className="muted">
        One prompt per strategy you want to compare. The run form picks a variant
        by name.
      </p>
      {draft.variants.length === 0 ? (
        <p className="muted">No prompt variants yet.</p>
      ) : null}
      {draft.variants.map((variant, index) => (
        <div className="case-variant" key={index}>
          <div className="form-grid">
            <label>
              <span>Variant name</span>
              <input
                disabled={readOnly}
                onChange={(event) =>
                  patch({
                    variants: draft.variants.map((item, position) =>
                      position === index
                        ? { ...item, name: event.target.value }
                        : item
                    )
                  })
                }
                placeholder="default"
                value={variant.name}
              />
            </label>
            {readOnly ? null : (
              <button
                className="badge"
                onClick={() =>
                  patch({
                    variants: draft.variants.filter(
                      (_item, position) => position !== index
                    )
                  })
                }
                type="button"
              >
                Remove
              </button>
            )}
          </div>
          <label>
            <span>Prompt</span>
            <textarea
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  variants: draft.variants.map((item, position) =>
                    position === index
                      ? { ...item, body: event.target.value }
                      : item
                  )
                })
              }
              rows={6}
              value={variant.body}
            />
          </label>
        </div>
      ))}

      <div className="panel-head">
        <h3>Step prompts</h3>
        {readOnly ? null : (
          <button
            className="badge"
            onClick={() => patch({ stepPrompts: [...draft.stepPrompts, ""] })}
            type="button"
          >
            Add step
          </button>
        )}
      </div>
      <p className="muted">
        Optional, for multi-step cases: step 1 uses the prompt variant, and each
        entry here drives the step with the same number.
      </p>
      {draft.stepPrompts.map((step, index) => (
        <label key={index}>
          <span>Step {index + 1}</span>
          <textarea
            disabled={readOnly}
            onChange={(event) =>
              patch({
                stepPrompts: draft.stepPrompts.map((item, position) =>
                  position === index ? event.target.value : item
                )
              })
            }
            rows={3}
            value={step}
          />
          {readOnly ? null : (
            <button
              className="badge"
              onClick={() =>
                patch({
                  stepPrompts: draft.stepPrompts.filter(
                    (_item, position) => position !== index
                  )
                })
              }
              type="button"
            >
              Remove step
            </button>
          )}
        </label>
      ))}

      <div className="panel-head">
        <h3>Success criteria</h3>
        {readOnly ? null : (
          <button
            className="badge"
            onClick={() =>
              patch({ criteria: [...draft.criteria, { key: "", value: "" }] })
            }
            type="button"
          >
            Add criterion
          </button>
        )}
      </div>
      {draft.criteria.map((row, index) => (
        <div className="form-grid" key={index}>
          <label>
            <span>Criterion</span>
            <input
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  criteria: draft.criteria.map((item, position) =>
                    position === index ? { ...item, key: event.target.value } : item
                  )
                })
              }
              value={row.key}
            />
          </label>
          <label>
            <span>Expected</span>
            <input
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  criteria: draft.criteria.map((item, position) =>
                    position === index ? { ...item, value: event.target.value } : item
                  )
                })
              }
              value={row.value}
            />
          </label>
          {readOnly ? null : (
            <button
              className="badge"
              onClick={() =>
                patch({
                  criteria: draft.criteria.filter(
                    (_item, position) => position !== index
                  )
                })
              }
              type="button"
            >
              Remove
            </button>
          )}
        </div>
      ))}

      <label>
        <span>Constraints (one per line)</span>
        <textarea
          disabled={readOnly}
          onChange={(event) => patch({ constraints: event.target.value })}
          rows={3}
          value={draft.constraints}
        />
      </label>

      <label>
        <span>Validation commands (one per line)</span>
        <textarea
          disabled={readOnly}
          onChange={(event) => patch({ validationCommands: event.target.value })}
          rows={3}
          value={draft.validationCommands}
        />
        <small className="muted">
          Run inside the sandbox after the agent finishes; a non-zero exit fails
          the objective score. <code>{"{run_id}"}</code> is substituted.
        </small>
      </label>

      <label>
        <span>Scoring rubric</span>
        <textarea
          disabled={readOnly}
          onChange={(event) => patch({ scoringRubric: event.target.value })}
          rows={3}
          value={draft.scoringRubric}
        />
      </label>

      <div className="toolbar">
        {readOnly ? null : (
          <button
            className="primary"
            disabled={busy !== null}
            onClick={handleSave}
            type="button"
          >
            {busy === "save" ? "Saving…" : "Save"}
          </button>
        )}
        <button disabled={busy !== null} onClick={handleSync} type="button">
          {busy === "sync" ? "Exporting…" : "Sync to YAML"}
        </button>
        <button disabled={busy !== null} onClick={handleClone} type="button">
          {busy === "clone" ? "Cloning…" : "Clone"}
        </button>
        <button disabled={busy !== null} onClick={handleDelete} type="button">
          {busy === "delete" ? "Deleting…" : "Delete"}
        </button>
      </div>
      {message ? <p className="muted">{message}</p> : null}
      {error ? <p className="badge error">{error}</p> : null}
    </section>
  );
}
