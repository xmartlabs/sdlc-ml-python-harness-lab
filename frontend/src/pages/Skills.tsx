import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { defineMessages, useMessages } from "../i18n";
import type {
  HarnessSkill,
  StagedSkillFileRecord,
  StagedSkillImportChoice,
  StagedSkillImportChoices,
  StagedSkillImportPreview,
  StagedSkillRecord
} from "../types";

const SKILL_SKELETON = `---
name: my-new-skill
description: One line saying when an agent should use this skill.
---

# My new skill

Explain the workflow the agent should follow, step by step.
Keep it concrete: commands to run, files to read, exit criteria.
`;

const STATUS_BADGE: Record<StagedSkillRecord["status"], string> = {
  draft: "badge",
  testing: "badge neutral",
  promoted: "badge ok",
  archived: "badge"
};

const MESSAGES = defineMessages({
  en: {
    eyebrow: "Skills",
    title: "Skill catalog & staging",
    lede: "Governed skills the harness ships with, plus a staging area to trial new skills before promoting them.",
    howto: {
      heading: "How to validate a new skill",
      step1: (
        <>
          Import a skill from a URL, upload an archive, or write one below. Multi-file skills
          (<code>scripts/</code>, <code>references/</code>) are supported.
        </>
      ),
      step2: (
        <>
          Test it from <strong>New evaluation</strong> (factor <strong>Add skills</strong>:
          baseline vs baseline + this skill) or from <strong>Validate your tool</strong>.
          Advanced → Runs also lists it under <strong>Experimental</strong>.
        </>
      ),
      step3: (
        <>
          Compare the two runs (validation score, attribution, LLM audit verdicts) — they have
          different <code>condition_hash</code> values, so they are distinct arms.
        </>
      ),
      step4: (
        <>
          If it earns its keep, <strong>Promote</strong>: the files land in
          <code> .github/skills-external/</code> and views re-sync. Review and commit manually.
        </>
      )
    },
    importSection: {
      heading: "Import a skill",
      hint: "nothing is written until you confirm the preview",
      sourceLabel: "Source — a URL, an install command, or the owner/repo shorthand",
      sourcePlaceholder:
        "https://github.com/anthropics/skills/tree/main/skills/frontend-design",
      sourceHelp: (
        <>
          Also accepted: <code>https://skills.sh/owner/repo/my-skill</code>,{" "}
          <code>npx skills add &lt;repo&gt; --skill &lt;name&gt;</code>,{" "}
          <code>owner/repo</code>, a link to a <code>SKILL.md</code>, or a
          .zip/.tar.gz archive. A repo holding several skills will ask which one.
        </>
      ),
      refLabel: "Ref (branch, tag or commit — optional)",
      refPlaceholder: "main",
      nameOverrideLabel: "Name override (optional)",
      nameOverridePlaceholder: "taken from the frontmatter",
      fetching: "Fetching...",
      previewImport: "Preview import",
      uploadLabel: "or upload a .md / .zip / .tar.gz",
      choicesIntro: (count: number) => (
        <>
          <strong>This source holds {count} skills.</strong> Pick the one to stage — nothing is
          written until you confirm its preview.
        </>
      ),
      noDescription: "no description",
      cancel: "Cancel",
      metaLine: (sourceKind: string, ref: string, fileCount: number, bytes: string) =>
        `${sourceKind}${ref ? ` @ ${ref}` : ""} · ${fileCount} file${fileCount === 1 ? "" : "s"} · ${bytes}`,
      replaceLabel: "Replace the staged skill if it already exists (discards local edits)",
      importing: "Importing...",
      importBtn: "Import"
    },
    form: {
      editHeading: (name: string) => `Edit staged skill: ${name}`,
      newHeading: "New staged skill",
      newInstead: "New instead",
      nameLabel: "Name (kebab-case)",
      namePlaceholder: "my-new-skill",
      descriptionLabel: "Description",
      descriptionPlaceholder: "When should an agent reach for this skill?",
      contentLabel: "SKILL.md content",
      addFilePlaceholder: "scripts/run.sh",
      addFile: "Add file",
      binaryAsset: (path: ReactNode, size: string) => (
        <>
          {path} is a binary asset ({size}); it is stored and materialized but cannot be edited
          here.
        </>
      ),
      saving: "Saving...",
      saveFile: (path: string) => `Save ${path}`,
      saveDescription: "Save description",
      createSkill: "Create staged skill"
    },
    staged: {
      heading: "Staged skills",
      total: (count: number) => `${count} total`,
      emptyTitle: "No staged skills yet.",
      emptyDescription:
        "Import one above, then A/B it from New evaluation (factor 'Add skills') or Validate your tool.",
      noDescription: "no description",
      edit: "Edit",
      promote: "Promote",
      archive: "Archive",
      unarchive: "Unarchive",
      delete: "Delete",
      status: {
        draft: "draft",
        testing: "testing",
        promoted: "promoted",
        archived: "archived"
      },
      confirmDelete: (name: string) => `Delete staged skill "${name}"? This cannot be undone.`,
      confirmPromote: (name: string) =>
        `Promote "${name}" into .github/skills-external/? Files are written and views re-synced, but nothing is committed — you review and commit.`,
      confirmDeleteFile: (path: string, skill: string) => `Delete "${path}" from ${skill}?`
    },
    governed: {
      heading: "Governed catalog",
      count: (count: number) => `${count} skills (read-only)`,
      noDescription: "no description",
      editVia: "edit via .github/skills + make sync-skills"
    },
    notices: {
      savedDescription: (name: string) =>
        `Saved ${name}. Runs that include it will pick up the new content (and a new condition_hash).`,
      created: (name: string) =>
        `Created ${name}. Test it from New evaluation (factor 'Add skills': baseline vs baseline + this skill) or from Validate your tool. Advanced → Runs also lists it under Experimental.`,
      promoted: (name: string, count: number, nextSteps: string) =>
        `Promoted ${name}: wrote ${count} file${count === 1 ? "" : "s"}. ${nextSteps}`,
      savedFile: (path: string) =>
        `Saved ${path}. The skill's content hash changed, so it is a new arm.`,
      imported: (name: string, count: number) =>
        `Imported ${name} (${count} file${count === 1 ? "" : "s"}). Test it from New evaluation (factor 'Add skills': baseline vs baseline + this skill) or from Validate your tool. Advanced → Runs also lists it under Experimental.`
    }
  },
  es: {
    eyebrow: "Skills",
    title: "Catálogo y preparación de skills",
    lede: "Skills gobernadas que vienen con el arnés, más un área de preparación para probar skills nuevas antes de promoverlas.",
    howto: {
      heading: "Cómo validar una skill nueva",
      step1: (
        <>
          Importá una skill desde una URL, subí un archivo comprimido, o escribí una abajo. Se
          admiten skills multiarchivo (<code>scripts/</code>, <code>references/</code>).
        </>
      ),
      step2: (
        <>
          Probala desde <strong>Nueva evaluación</strong> (factor <strong>Agregar skills</strong>:
          base vs. base + esta skill) o desde <strong>Validá tu herramienta</strong>.
          Avanzado → Corridas también la lista en <strong>Experimental</strong>.
        </>
      ),
      step3: (
        <>
          Comparó las dos corridas (puntaje de validación, atribución, veredictos de auditoría
          LLM) — tienen valores de <code>condition_hash</code> distintos, así que son brazos
          distintos.
        </>
      ),
      step4: (
        <>
          Si se gana su lugar, <strong>Promové</strong>: los archivos quedan en
          <code> .github/skills-external/</code> y las vistas se resincronizan. Revisá y
          commiteá manualmente.
        </>
      )
    },
    importSection: {
      heading: "Importar una skill",
      hint: "nada se escribe hasta que confirmes la vista previa",
      sourceLabel: "Origen — una URL, un comando de instalación, o el atajo owner/repo",
      sourcePlaceholder:
        "https://github.com/anthropics/skills/tree/main/skills/frontend-design",
      sourceHelp: (
        <>
          También se acepta: <code>https://skills.sh/owner/repo/my-skill</code>,{" "}
          <code>npx skills add &lt;repo&gt; --skill &lt;name&gt;</code>,{" "}
          <code>owner/repo</code>, un link a un <code>SKILL.md</code>, o un
          archivo .zip/.tar.gz. Un repo con varias skills va a preguntar cuál.
        </>
      ),
      refLabel: "Ref (rama, tag o commit — opcional)",
      refPlaceholder: "main",
      nameOverrideLabel: "Nombre alternativo (opcional)",
      nameOverridePlaceholder: "tomado del frontmatter",
      fetching: "Buscando...",
      previewImport: "Previsualizar importación",
      uploadLabel: "o subí un .md / .zip / .tar.gz",
      choicesIntro: (count: number) => (
        <>
          <strong>Este origen tiene {count} skills.</strong> Elegí la que querés preparar —
          nada se escribe hasta que confirmes su vista previa.
        </>
      ),
      noDescription: "sin descripción",
      cancel: "Cancelar",
      metaLine: (sourceKind: string, ref: string, fileCount: number, bytes: string) =>
        `${sourceKind}${ref ? ` @ ${ref}` : ""} · ${fileCount} archivo${fileCount === 1 ? "" : "s"} · ${bytes}`,
      replaceLabel: "Reemplazar la skill preparada si ya existe (descarta las ediciones locales)",
      importing: "Importando...",
      importBtn: "Importar"
    },
    form: {
      editHeading: (name: string) => `Editar skill preparada: ${name}`,
      newHeading: "Nueva skill preparada",
      newInstead: "Nueva en su lugar",
      nameLabel: "Nombre (kebab-case)",
      namePlaceholder: "my-new-skill",
      descriptionLabel: "Descripción",
      descriptionPlaceholder: "¿Cuándo debería un agente recurrir a esta skill?",
      contentLabel: "Contenido de SKILL.md",
      addFilePlaceholder: "scripts/run.sh",
      addFile: "Agregar archivo",
      binaryAsset: (path: ReactNode, size: string) => (
        <>
          {path} es un archivo binario ({size}); se almacena y materializa pero no se puede
          editar acá.
        </>
      ),
      saving: "Guardando...",
      saveFile: (path: string) => `Guardar ${path}`,
      saveDescription: "Guardar descripción",
      createSkill: "Crear skill preparada"
    },
    staged: {
      heading: "Skills preparadas",
      total: (count: number) => `${count} en total`,
      emptyTitle: "Todavía no hay skills preparadas.",
      emptyDescription:
        "Importá una arriba y probala con A/B desde Nueva evaluación (factor 'Agregar skills') o Validá tu herramienta.",
      noDescription: "sin descripción",
      edit: "Editar",
      promote: "Promover",
      archive: "Archivar",
      unarchive: "Desarchivar",
      delete: "Eliminar",
      status: {
        draft: "borrador",
        testing: "en prueba",
        promoted: "promovida",
        archived: "archivada"
      },
      confirmDelete: (name: string) =>
        `¿Eliminar la skill preparada "${name}"? Esto no se puede deshacer.`,
      confirmPromote: (name: string) =>
        `¿Promover "${name}" a .github/skills-external/? Los archivos se escriben y las vistas se resincronizan, pero nada se commitea — vos revisás y commiteás.`,
      confirmDeleteFile: (path: string, skill: string) => `¿Eliminar "${path}" de ${skill}?`
    },
    governed: {
      heading: "Catálogo gobernado",
      count: (count: number) => `${count} skills (de solo lectura)`,
      noDescription: "sin descripción",
      editVia: "editar vía .github/skills + make sync-skills"
    },
    notices: {
      savedDescription: (name: string) =>
        `Se guardó ${name}. Las corridas que la incluyan van a tomar el contenido nuevo (y un condition_hash nuevo).`,
      created: (name: string) =>
        `Se creó ${name}. Probala desde Nueva evaluación (factor 'Agregar skills': base vs. base + esta skill) o desde Validá tu herramienta. Avanzado → Corridas también la lista en Experimental.`,
      promoted: (name: string, count: number, nextSteps: string) =>
        `Se promovió ${name}: se escribieron ${count} archivo${count === 1 ? "" : "s"}. ${nextSteps}`,
      savedFile: (path: string) =>
        `Se guardó ${path}. El hash de contenido de la skill cambió, así que es un brazo nuevo.`,
      imported: (name: string, count: number) =>
        `Se importó ${name} (${count} archivo${count === 1 ? "" : "s"}). Probala desde Nueva evaluación (factor 'Agregar skills': base vs. base + esta skill) o desde Validá tu herramienta. Avanzado → Corridas también la lista en Experimental.`
    }
  }
});

function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KiB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
}

type ImportResult =
  | StagedSkillImportPreview
  | StagedSkillImportChoices
  | StagedSkillRecord;

function isPreview(value: ImportResult): value is StagedSkillImportPreview {
  return (value as StagedSkillImportPreview).preview === true;
}

/** A catalog source came back: several skills, none picked yet. */
function isChoices(value: ImportResult): value is StagedSkillImportChoices {
  const candidate = value as StagedSkillImportChoices;
  return candidate.preview === false && Array.isArray(candidate.choices);
}

export function Skills() {
  const m = useMessages(MESSAGES);
  const [governed, setGoverned] = useState<HarnessSkill[]>([]);
  const [staged, setStaged] = useState<StagedSkillRecord[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState(SKILL_SKELETON);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Import (URL or upload). The preview is what the user reviews before
  // anything is written; until they confirm, the server has stored nothing.
  const [importUrl, setImportUrl] = useState("");
  const [importRef, setImportRef] = useState("");
  const [importName, setImportName] = useState("");
  const [replace, setReplace] = useState(false);
  const [preview, setPreview] = useState<StagedSkillImportPreview | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  // Set once a catalog source has been narrowed to one skill; it has to travel
  // with the confirm call too, or the server would face the same ambiguity.
  const [importSkill, setImportSkill] = useState("");
  const [choices, setChoices] = useState<StagedSkillImportChoice[] | null>(null);

  // File browser for the skill being edited.
  const [files, setFiles] = useState<StagedSkillFileRecord[]>([]);
  const [activePath, setActivePath] = useState<string>("");
  const [fileContent, setFileContent] = useState("");
  const [newFilePath, setNewFilePath] = useState("");

  const refresh = () => {
    api.listHarnessSkills().then(setGoverned).catch(() => {});
    api.listStagedSkills().then(setStaged).catch(() => {});
  };

  useEffect(refresh, []);

  const resetForm = () => {
    setEditing(null);
    setName("");
    setDescription("");
    setContent(SKILL_SKELETON);
    setFiles([]);
    setActivePath("");
    setFileContent("");
    setNewFilePath("");
  };

  const loadFiles = (skillName: string, select?: string) => {
    api
      .listStagedSkillFiles(skillName)
      .then((rows) => {
        setFiles(rows);
        const target = select ?? activePath;
        const chosen = rows.find((row) => row.path === target) ?? rows[0];
        if (chosen) {
          setActivePath(chosen.path);
          setFileContent(chosen.encoding === "base64" ? "" : chosen.content);
        }
      })
      .catch((err: Error) => setError(err.message));
  };

  const handleSave = () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    const action = editing
      ? api.updateStagedSkill(editing, { description, content })
      : api.createStagedSkill(name.trim(), description, content);
    action
      .then((saved) => {
        setNotice(
          editing ? m.notices.savedDescription(saved.name) : m.notices.created(saved.name)
        );
        resetForm();
        refresh();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handleEdit = (skill: StagedSkillRecord) => {
    setEditing(skill.name);
    setName(skill.name);
    setDescription(skill.description);
    setContent(skill.content);
    setActivePath("");
    loadFiles(skill.name, "SKILL.md");
  };

  const handleStatus = (skill: StagedSkillRecord, status: string) => {
    setBusy(true);
    setError(null);
    api
      .updateStagedSkill(skill.name, { status })
      .then(refresh)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handleDelete = (skill: StagedSkillRecord) => {
    if (!window.confirm(m.staged.confirmDelete(skill.name))) {
      return;
    }
    setBusy(true);
    setError(null);
    api
      .deleteStagedSkill(skill.name)
      .then(() => {
        if (editing === skill.name) {
          resetForm();
        }
        refresh();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handlePromote = (skill: StagedSkillRecord) => {
    if (!window.confirm(m.staged.confirmPromote(skill.name))) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    api
      .promoteStagedSkill(skill.name)
      .then((result) => {
        setNotice(m.notices.promoted(skill.name, result.written.length, result.next_steps));
        refresh();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const applyImportResult = (result: ImportResult) => {
    if (isChoices(result)) {
      setChoices(result.choices);
      setPreview(null);
      return;
    }
    if (isPreview(result)) {
      setChoices(null);
      setPreview(result);
    }
  };

  const handlePreviewUrl = (skill = importSkill) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    setPendingFile(null);
    setImportSkill(skill);
    api
      .previewStagedSkillImport(
        importUrl.trim(),
        importRef.trim(),
        importName.trim(),
        skill
      )
      .then(applyImportResult)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handlePreviewUpload = (file: File, skill = importSkill) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    setPendingFile(file);
    setImportSkill(skill);
    api
      .uploadStagedSkill(file, { name: importName.trim(), skill })
      .then(applyImportResult)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handlePickSkill = (skill: string) => {
    if (pendingFile) {
      handlePreviewUpload(pendingFile, skill);
    } else {
      handlePreviewUrl(skill);
    }
  };

  const cancelPreview = () => {
    setPreview(null);
    setChoices(null);
    setImportSkill("");
    setPendingFile(null);
    setReplace(false);
  };

  const handleConfirmImport = () => {
    if (!preview) {
      return;
    }
    setBusy(true);
    setError(null);
    const action = pendingFile
      ? api.uploadStagedSkill(pendingFile, {
          name: importName.trim(),
          skill: importSkill,
          confirm: true,
          replace
        })
      : api.importStagedSkill(
          importUrl.trim(),
          importRef.trim(),
          importName.trim(),
          replace,
          importSkill
        );
    action
      .then(() => {
        setNotice(m.notices.imported(preview.name, preview.files.length));
        cancelPreview();
        setImportUrl("");
        setImportRef("");
        setImportName("");
        refresh();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handleSelectFile = (row: StagedSkillFileRecord) => {
    setActivePath(row.path);
    setFileContent(row.encoding === "base64" ? "" : row.content);
  };

  const handleSaveFile = () => {
    if (!editing || !activePath) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    api
      .writeStagedSkillFile(editing, activePath, fileContent)
      .then(() => {
        setNotice(m.notices.savedFile(activePath));
        loadFiles(editing, activePath);
        refresh();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handleAddFile = () => {
    const path = newFilePath.trim();
    if (!editing || path.length === 0) {
      return;
    }
    setBusy(true);
    setError(null);
    api
      .writeStagedSkillFile(editing, path, "")
      .then(() => {
        setNewFilePath("");
        loadFiles(editing, path);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handleDeleteFile = (path: string) => {
    if (!editing || !window.confirm(m.staged.confirmDeleteFile(path, editing))) {
      return;
    }
    setBusy(true);
    setError(null);
    api
      .deleteStagedSkillFile(editing, path)
      .then(() => loadFiles(editing, "SKILL.md"))
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const activeFile = files.find((row) => row.path === activePath);

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{m.eyebrow}</p>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.howto.heading}</h2>
          <span className="muted">docs/guide-skill-validation.md</span>
        </div>
        <ol className="skills-howto">
          <li>{m.howto.step1}</li>
          <li>{m.howto.step2}</li>
          <li>{m.howto.step3}</li>
          <li>{m.howto.step4}</li>
        </ol>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.importSection.heading}</h2>
          <span className="muted">{m.importSection.hint}</span>
        </div>
        <div className="form-grid">
          <label className="full">
            <span>{m.importSection.sourceLabel}</span>
            <input
              onChange={(event) => setImportUrl(event.target.value)}
              placeholder={m.importSection.sourcePlaceholder}
              value={importUrl}
            />
            <small className="muted">{m.importSection.sourceHelp}</small>
          </label>
          <label>
            <span>{m.importSection.refLabel}</span>
            <input
              onChange={(event) => setImportRef(event.target.value)}
              placeholder={m.importSection.refPlaceholder}
              value={importRef}
            />
          </label>
          <label>
            <span>{m.importSection.nameOverrideLabel}</span>
            <input
              onChange={(event) => setImportName(event.target.value)}
              placeholder={m.importSection.nameOverridePlaceholder}
              value={importName}
            />
          </label>
        </div>
        <div className="panel-actions">
          <button
            className="primary"
            disabled={busy || importUrl.trim().length === 0}
            onClick={() => handlePreviewUrl("")}
            type="button"
          >
            {busy ? m.importSection.fetching : m.importSection.previewImport}
          </button>
          <label className="upload-label">
            <span>{m.importSection.uploadLabel}</span>
            <input
              accept=".md,.zip,.tar,.tar.gz,.tgz"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  handlePreviewUpload(file, "");
                }
                event.target.value = "";
              }}
              type="file"
            />
          </label>
        </div>

        {choices ? (
          <div className="import-preview">
            <p>{m.importSection.choicesIntro(choices.length)}</p>
            <ul className="choice-list">
              {choices.map((choice) => (
                <li key={choice.path || choice.name}>
                  <button
                    className="choice"
                    disabled={busy}
                    onClick={() => handlePickSkill(choice.name)}
                    type="button"
                  >
                    <strong>{choice.name}</strong>
                    <span className="muted">
                      {choice.description || choice.path || m.importSection.noDescription}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="panel-actions">
              <button disabled={busy} onClick={cancelPreview} type="button">
                {m.importSection.cancel}
              </button>
            </div>
          </div>
        ) : null}

        {preview ? (
          <div className="import-preview">
            <p>
              <strong>{preview.name}</strong> — {preview.description || m.importSection.noDescription}
            </p>
            <p className="muted">
              {m.importSection.metaLine(
                preview.source_kind,
                preview.source_ref,
                preview.files.length,
                formatBytes(preview.total_bytes)
              )}
            </p>
            {preview.warnings.length > 0 ? (
              <ul className="warning-list">
                {preview.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
            <ul className="file-list">
              {preview.files.map((file) => (
                <li key={file.path}>
                  <code>{file.path}</code> <span className="muted">{formatBytes(file.size)}</span>
                </li>
              ))}
            </ul>
            <label className="inline-check">
              <input
                checked={replace}
                onChange={(event) => setReplace(event.target.checked)}
                type="checkbox"
              />
              <span>{m.importSection.replaceLabel}</span>
            </label>
            <div className="panel-actions">
              <button className="primary" disabled={busy} onClick={handleConfirmImport} type="button">
                {busy ? m.importSection.importing : m.importSection.importBtn}
              </button>
              <button disabled={busy} onClick={cancelPreview} type="button">
                {m.importSection.cancel}
              </button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{editing ? m.form.editHeading(editing) : m.form.newHeading}</h2>
          {editing ? (
            <button onClick={resetForm} type="button">
              {m.form.newInstead}
            </button>
          ) : null}
        </div>
        <div className="form-grid">
          <label>
            <span>{m.form.nameLabel}</span>
            <input
              disabled={editing !== null}
              onChange={(event) => setName(event.target.value)}
              placeholder={m.form.namePlaceholder}
              value={name}
            />
          </label>
          <label>
            <span>{m.form.descriptionLabel}</span>
            <input
              onChange={(event) => setDescription(event.target.value)}
              placeholder={m.form.descriptionPlaceholder}
              value={description}
            />
          </label>
          {editing ? null : (
            <label className="full">
              <span>{m.form.contentLabel}</span>
              <textarea
                className="skill-editor"
                onChange={(event) => setContent(event.target.value)}
                value={content}
              />
            </label>
          )}
        </div>

        {editing ? (
          <div className="skill-files">
            <div className="file-column">
              <ul className="file-list">
                {files.map((row) => (
                  <li className={row.path === activePath ? "active" : ""} key={row.path}>
                    <button onClick={() => handleSelectFile(row)} type="button">
                      {row.path}
                    </button>
                    <span className="muted">{formatBytes(row.size)}</span>
                    {row.path === "SKILL.md" ? null : (
                      <button
                        disabled={busy}
                        onClick={() => handleDeleteFile(row.path)}
                        type="button"
                      >
                        ✕
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              <div className="file-add">
                <input
                  onChange={(event) => setNewFilePath(event.target.value)}
                  placeholder={m.form.addFilePlaceholder}
                  value={newFilePath}
                />
                <button
                  disabled={busy || newFilePath.trim().length === 0}
                  onClick={handleAddFile}
                  type="button"
                >
                  {m.form.addFile}
                </button>
              </div>
            </div>
            <div className="file-editor">
              {activeFile && activeFile.encoding === "base64" ? (
                <p className="muted">
                  {m.form.binaryAsset(<code>{activeFile.path}</code>, formatBytes(activeFile.size))}
                </p>
              ) : (
                <textarea
                  className="skill-editor"
                  onChange={(event) => setFileContent(event.target.value)}
                  value={fileContent}
                />
              )}
            </div>
          </div>
        ) : null}

        <div className="panel-actions">
          {editing ? (
            <>
              <button
                className="primary"
                disabled={busy || !activePath || activeFile?.encoding === "base64"}
                onClick={handleSaveFile}
                type="button"
              >
                {busy ? m.form.saving : m.form.saveFile(activePath || "file")}
              </button>
              <button disabled={busy} onClick={handleSave} type="button">
                {m.form.saveDescription}
              </button>
            </>
          ) : (
            <button
              className="primary"
              disabled={busy || name.trim().length === 0}
              onClick={handleSave}
              type="button"
            >
              {busy ? m.form.saving : m.form.createSkill}
            </button>
          )}
        </div>
        {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
        {notice ? <p className="muted">{notice}</p> : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.staged.heading}</h2>
          <span className="muted">{m.staged.total(staged.length)}</span>
        </div>
        {staged.length === 0 ? (
          <EmptyState description={m.staged.emptyDescription} title={m.staged.emptyTitle} />
        ) : (
          <div className="table table--staged">
            {staged.map((skill) => (
              <div className="table-row harness-row" key={skill.name}>
                <span className="harness-name">
                  <strong>{skill.name}</strong>
                  <small>{skill.description || m.staged.noDescription}</small>
                  {skill.source_url ? (
                    <small className="muted">
                      {skill.source_kind}: {skill.source_url}
                      {skill.source_ref ? ` @ ${skill.source_ref}` : ""}
                    </small>
                  ) : null}
                </span>
                <span className={STATUS_BADGE[skill.status]}>{m.staged.status[skill.status]}</span>
                <span className="row-actions">
                  <button disabled={busy} onClick={() => handleEdit(skill)} type="button">
                    {m.staged.edit}
                  </button>
                  {skill.status !== "promoted" ? (
                    <button disabled={busy} onClick={() => handlePromote(skill)} type="button">
                      {m.staged.promote}
                    </button>
                  ) : null}
                  <button
                    disabled={busy}
                    onClick={() =>
                      handleStatus(skill, skill.status === "archived" ? "draft" : "archived")
                    }
                    type="button"
                  >
                    {skill.status === "archived" ? m.staged.unarchive : m.staged.archive}
                  </button>
                  <button disabled={busy} onClick={() => handleDelete(skill)} type="button">
                    {m.staged.delete}
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.governed.heading}</h2>
          <span className="muted">{m.governed.count(governed.length)}</span>
        </div>
        <div className="table">
          {governed.map((skill) => (
            <div className="table-row harness-row" key={skill.name}>
              <span className="harness-name">
                <strong>{skill.name}</strong>
                <small>{skill.description || m.governed.noDescription}</small>
              </span>
              <span className="badge neutral">{skill.kind}</span>
              <span className="muted">{m.governed.editVia}</span>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}
