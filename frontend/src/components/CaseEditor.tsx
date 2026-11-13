import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api";
import { SWE_CASE_TYPES, uninstallableRequirements } from "../caseLabels";
import { defineMessages, useMessages } from "../i18n";
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

const MESSAGES = defineMessages({
  en: {
    editCase: "Edit case",
    readOnlyNotice:
      "This case was imported from the SWE-bench dataset, so its definition is " +
      "read-only — editing the prompts or test lists would invalidate the " +
      "benchmark. Clone it to build a variant you can change.",
    caseId: "Case ID",
    caseIdHint: "Fixed: it is the key every run in the history joins on.",
    name: "Name",
    type: "Type",
    maxSteps: "Max steps",
    description: "Description",
    promptVariants: "Prompt variants",
    addVariant: "Add variant",
    promptVariantsHint:
      "One prompt per strategy you want to compare. The run form picks a variant by name.",
    noVariants: "No prompt variants yet.",
    variantName: "Variant name",
    remove: "Remove",
    prompt: "Prompt",
    stepPrompts: "Step prompts",
    addStep: "Add step",
    stepPromptsHint:
      "Optional, for multi-step cases: step 1 uses the prompt variant, and each " +
      "entry here drives the step with the same number.",
    step: (index: number) => `Step ${index}`,
    removeStep: "Remove step",
    successCriteria: "Success criteria",
    addCriterion: "Add criterion",
    criterion: "Criterion",
    expected: "Expected",
    constraintsLabel: "Constraints (one per line)",
    validationCommandsLabel: "Validation commands (one per line)",
    validationCommandsHint: (runId: ReactNode) => (
      <>
        Run inside the sandbox after the agent finishes; a non-zero exit fails the
        objective score. {runId} is substituted.
      </>
    ),
    validationRequirementsLabel: "Grader requirements (pinned, one per line)",
    validationRequirementsHint:
      "Installed before the checks run, which is offline in a clean container " +
      "with only the standard library, pytest, pyyaml and requests.",
    requirementsNotInstallable: (lines: string) =>
      `Not installable, so the setup step would skip it: ${lines}. Use a ` +
      "package name with an optional version (numpy==2.4.6, pandas>=2.2).",
    scoringRubric: "Scoring rubric",
    saving: "Saving…",
    save: "Save",
    exporting: "Exporting…",
    syncToYaml: "Sync to YAML",
    cloning: "Cloning…",
    clone: "Clone",
    deleting: "Deleting…",
    deleteAction: "Delete",
    saved: "Saved.",
    exportedTo: (path: string) => `Exported to ${path}`,
    newCaseIdPrompt: "New case id",
    cloneCancelled: "Clone cancelled.",
    clonedAs: (caseId: string) => `Cloned as ${caseId}.`,
    deleteConfirm: (caseId: string) => `Delete case ${caseId}? Its runs are kept.`,
    deleteCancelled: "Delete cancelled.",
    deletedCase: (caseId: string) => `Deleted ${caseId}.`
  },
  es: {
    editCase: "Editar caso",
    readOnlyNotice:
      "Este caso se importó del dataset de SWE-bench, así que su definición es " +
      "de solo lectura — editar los prompts o las listas de tests invalidaría el " +
      "benchmark. Cloná el caso para construir una variante que puedas cambiar.",
    caseId: "ID del caso",
    caseIdHint: "Fijo: es la clave con la que se relaciona cada corrida del historial.",
    name: "Nombre",
    type: "Tipo",
    maxSteps: "Pasos máximos",
    description: "Descripción",
    promptVariants: "Variantes de prompt",
    addVariant: "Agregar variante",
    promptVariantsHint:
      "Un prompt por cada estrategia que quieras comparar. El formulario de " +
      "corrida elige una variante por nombre.",
    noVariants: "Todavía no hay variantes de prompt.",
    variantName: "Nombre de la variante",
    remove: "Eliminar",
    prompt: "Prompt",
    stepPrompts: "Prompts de paso",
    addStep: "Agregar paso",
    stepPromptsHint:
      "Opcional, para casos multi-paso: el paso 1 usa la variante de prompt, y " +
      "cada entrada acá maneja el paso con el mismo número.",
    step: (index: number) => `Paso ${index}`,
    removeStep: "Eliminar paso",
    successCriteria: "Criterios de éxito",
    addCriterion: "Agregar criterio",
    criterion: "Criterio",
    expected: "Esperado",
    constraintsLabel: "Restricciones (una por línea)",
    validationCommandsLabel: "Comandos de validación (uno por línea)",
    validationCommandsHint: (runId: ReactNode) => (
      <>
        Corre dentro del sandbox cuando el agente termina; una salida distinta de
        cero hace fallar el puntaje objetivo. Se sustituye {runId}.
      </>
    ),
    validationRequirementsLabel: "Dependencias del evaluador (fijadas, una por línea)",
    validationRequirementsHint:
      "Se instalan antes de los checks, que corren sin red en un contenedor " +
      "limpio con solo la biblioteca estándar, pytest, pyyaml y requests.",
    requirementsNotInstallable: (lines: string) =>
      `No se puede instalar, el paso de setup lo saltearía: ${lines}. Usá un ` +
      "nombre de paquete con versión opcional (numpy==2.4.6, pandas>=2.2).",
    scoringRubric: "Rúbrica de puntuación",
    saving: "Guardando…",
    save: "Guardar",
    exporting: "Exportando…",
    syncToYaml: "Sincronizar a YAML",
    cloning: "Clonando…",
    clone: "Clonar",
    deleting: "Eliminando…",
    deleteAction: "Eliminar",
    saved: "Guardado.",
    exportedTo: (path: string) => `Exportado a ${path}`,
    newCaseIdPrompt: "Nuevo id del caso",
    cloneCancelled: "Clonación cancelada.",
    clonedAs: (caseId: string) => `Clonado como ${caseId}.`,
    deleteConfirm: (caseId: string) => `¿Eliminar el caso ${caseId}? Sus corridas se conservan.`,
    deleteCancelled: "Eliminación cancelada.",
    deletedCase: (caseId: string) => `Se eliminó ${caseId}.`
  }
});

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
  validationRequirements: string;
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
    validationCommands: (item.validation_commands ?? []).join("\n"),
    validationRequirements: (
      item.metadata_json?.validation_requirements ?? []
    ).join("\n")
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
    // Only the key this form owns: the server merges it into the stored
    // metadata, so the starter dir and a SWE case's data are kept.
    metadata_json: {
      validation_requirements: toLines(draft.validationRequirements)
    },
    max_steps: Number.isFinite(draft.maxSteps) ? draft.maxSteps : 1
  };
}

export function CaseEditor({
  caseRecord,
  onSaved,
  onCloned,
  onDeleted
}: CaseEditorProps) {
  const m = useMessages(MESSAGES);
  const caseId = caseRecord.case_id;
  const [draft, setDraft] = useState<Draft>(() => toDraft(caseRecord));
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // SWE-bench cases mirror an imported dataset: their prompts and test lists
  // define the benchmark, so editing them would silently invalidate every
  // comparison already recorded against the case.
  const readOnly = SWE_CASE_TYPES.has(caseRecord.type);
  // What the setup step would skip; the server refuses it too, this says so first.
  const badRequirements = uninstallableRequirements(draft.validationRequirements);

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
      return m.saved;
    });

  const handleSync = () =>
    withBusy("sync", async () => {
      const result = await api.syncCase(caseId);
      const fresh = await api.getCase(caseId);
      onSaved(fresh);
      return m.exportedTo(result.task_path);
    });

  const handleClone = () =>
    withBusy("clone", async () => {
      const newCaseId = window.prompt(m.newCaseIdPrompt, `${caseId}-copy`);
      if (!newCaseId) {
        return m.cloneCancelled;
      }
      const created = await api.cloneCase(
        caseId,
        newCaseId.trim(),
        `${draft.name || caseId} (copy)`
      );
      onCloned(created);
      return m.clonedAs(created.case_id);
    });

  const handleDelete = () =>
    withBusy("delete", async () => {
      if (!window.confirm(m.deleteConfirm(caseId))) {
        return m.deleteCancelled;
      }
      await api.deleteCase(caseId);
      onDeleted(caseId);
      return m.deletedCase(caseId);
    });

  const patch = (changes: Partial<Draft>) =>
    setDraft((current) => ({ ...current, ...changes }));

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{m.editCase}</h2>
        <span className="badge neutral">{caseRecord.sync_state}</span>
      </div>
      {readOnly ? <p className="muted">{m.readOnlyNotice}</p> : null}

      <div className="form-grid">
        <label>
          <span>{m.caseId}</span>
          <input readOnly value={caseId} />
          <small className="muted">{m.caseIdHint}</small>
        </label>
        <label>
          <span>{m.name}</span>
          <input
            disabled={readOnly}
            onChange={(event) => patch({ name: event.target.value })}
            value={draft.name}
          />
        </label>
        <label>
          <span>{m.type}</span>
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
          <span>{m.maxSteps}</span>
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
        <span>{m.description}</span>
        <textarea
          disabled={readOnly}
          onChange={(event) => patch({ description: event.target.value })}
          rows={2}
          value={draft.description}
        />
      </label>

      <div className="panel-head">
        <h3>{m.promptVariants}</h3>
        {readOnly ? null : (
          <button
            className="badge"
            onClick={() =>
              patch({ variants: [...draft.variants, { name: "", body: "" }] })
            }
            type="button"
          >
            {m.addVariant}
          </button>
        )}
      </div>
      <p className="muted">{m.promptVariantsHint}</p>
      {draft.variants.length === 0 ? (
        <p className="muted">{m.noVariants}</p>
      ) : null}
      {draft.variants.map((variant, index) => (
        <div className="case-variant" key={index}>
          <div className="form-grid">
            <label>
              <span>{m.variantName}</span>
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
                {m.remove}
              </button>
            )}
          </div>
          <label>
            <span>{m.prompt}</span>
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
        <h3>{m.stepPrompts}</h3>
        {readOnly ? null : (
          <button
            className="badge"
            onClick={() => patch({ stepPrompts: [...draft.stepPrompts, ""] })}
            type="button"
          >
            {m.addStep}
          </button>
        )}
      </div>
      <p className="muted">{m.stepPromptsHint}</p>
      {draft.stepPrompts.map((step, index) => (
        <label key={index}>
          <span>{m.step(index + 1)}</span>
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
              {m.removeStep}
            </button>
          )}
        </label>
      ))}

      <div className="panel-head">
        <h3>{m.successCriteria}</h3>
        {readOnly ? null : (
          <button
            className="badge"
            onClick={() =>
              patch({ criteria: [...draft.criteria, { key: "", value: "" }] })
            }
            type="button"
          >
            {m.addCriterion}
          </button>
        )}
      </div>
      {draft.criteria.map((row, index) => (
        <div className="form-grid" key={index}>
          <label>
            <span>{m.criterion}</span>
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
            <span>{m.expected}</span>
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
              {m.remove}
            </button>
          )}
        </div>
      ))}

      <label>
        <span>{m.constraintsLabel}</span>
        <textarea
          disabled={readOnly}
          onChange={(event) => patch({ constraints: event.target.value })}
          rows={3}
          value={draft.constraints}
        />
      </label>

      <label>
        <span>{m.validationCommandsLabel}</span>
        <textarea
          disabled={readOnly}
          onChange={(event) => patch({ validationCommands: event.target.value })}
          rows={3}
          value={draft.validationCommands}
        />
        <small className="muted">
          {m.validationCommandsHint(<code>{"{run_id}"}</code>)}
        </small>
      </label>

      <label>
        <span>{m.validationRequirementsLabel}</span>
        <textarea
          disabled={readOnly}
          onChange={(event) =>
            patch({ validationRequirements: event.target.value })
          }
          placeholder={"numpy==2.4.6\npandas==3.1.0"}
          rows={2}
          value={draft.validationRequirements}
        />
        {badRequirements.length > 0 ? (
          <span className="error-text">
            {m.requirementsNotInstallable(badRequirements.join(", "))}
          </span>
        ) : (
          <small className="muted">{m.validationRequirementsHint}</small>
        )}
      </label>

      <label>
        <span>{m.scoringRubric}</span>
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
            disabled={busy !== null || badRequirements.length > 0}
            onClick={handleSave}
            type="button"
          >
            {busy === "save" ? m.saving : m.save}
          </button>
        )}
        <button disabled={busy !== null} onClick={handleSync} type="button">
          {busy === "sync" ? m.exporting : m.syncToYaml}
        </button>
        <button disabled={busy !== null} onClick={handleClone} type="button">
          {busy === "clone" ? m.cloning : m.clone}
        </button>
        <button disabled={busy !== null} onClick={handleDelete} type="button">
          {busy === "delete" ? m.deleting : m.deleteAction}
        </button>
      </div>
      {message ? <p className="muted">{message}</p> : null}
      {error ? <p className="badge error">{error}</p> : null}
    </section>
  );
}
