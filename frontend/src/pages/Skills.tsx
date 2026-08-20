import { useEffect, useState } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
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
          editing
            ? `Saved ${saved.name}. Runs that include it will pick up the new content (and a new condition_hash).`
            : `Created ${saved.name}. Enable it under "Experimental" in the New run form to test it.`
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
    if (!window.confirm(`Delete staged skill "${skill.name}"? This cannot be undone.`)) {
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
    if (
      !window.confirm(
        `Promote "${skill.name}" into .github/skills-external/? Files are written and views re-synced, but nothing is committed — you review and commit.`
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    api
      .promoteStagedSkill(skill.name)
      .then((result) => {
        setNotice(
          `Promoted ${skill.name}: wrote ${result.written.length} file(s). ${result.next_steps}`
        );
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
        setNotice(
          `Imported ${preview.name} (${preview.files.length} file(s)). Enable it under "Experimental" in the New run form to A/B it.`
        );
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
        setNotice(`Saved ${activePath}. The skill's content hash changed, so it is a new arm.`);
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
    if (!editing || !window.confirm(`Delete "${path}" from ${editing}?`)) {
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
          <p className="eyebrow">Skills</p>
          <h1>Skill catalog &amp; staging</h1>
          <p className="lede">
            Governed skills the harness ships with, plus a staging area to trial new skills
            before promoting them.
          </p>
        </div>
      </header>

      <section className="panel">
        <div className="panel-head">
          <h2>How to validate a new skill</h2>
          <span className="muted">docs/guide-skill-validation.md</span>
        </div>
        <ol className="skills-howto">
          <li>
            Import a skill from a URL, upload an archive, or write one below. Multi-file skills
            (<code>scripts/</code>, <code>references/</code>) are supported.
          </li>
          <li>
            In the New run form, check it under <strong>Experimental</strong> and launch a run;
            launch a second, identical run without it as the baseline.
          </li>
          <li>
            Compare the two runs (validation score, attribution, LLM audit verdicts) — they have
            different <code>condition_hash</code> values, so they are distinct arms.
          </li>
          <li>
            If it earns its keep, <strong>Promote</strong>: the files land in
            <code> .github/skills-external/</code> and views re-sync. Review and commit manually.
          </li>
        </ol>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Import a skill</h2>
          <span className="muted">nothing is written until you confirm the preview</span>
        </div>
        <div className="form-grid">
          <label className="full">
            <span>Source — a URL, an install command, or the owner/repo shorthand</span>
            <input
              onChange={(event) => setImportUrl(event.target.value)}
              placeholder="https://github.com/anthropics/skills/tree/main/skills/frontend-design"
              value={importUrl}
            />
            <small className="muted">
              Also accepted: <code>https://skills.sh/owner/repo/my-skill</code>,{" "}
              <code>npx skills add &lt;repo&gt; --skill &lt;name&gt;</code>,{" "}
              <code>owner/repo</code>, a link to a <code>SKILL.md</code>, or a
              .zip/.tar.gz archive. A repo holding several skills will ask which one.
            </small>
          </label>
          <label>
            <span>Ref (branch, tag or commit — optional)</span>
            <input
              onChange={(event) => setImportRef(event.target.value)}
              placeholder="main"
              value={importRef}
            />
          </label>
          <label>
            <span>Name override (optional)</span>
            <input
              onChange={(event) => setImportName(event.target.value)}
              placeholder="taken from the frontmatter"
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
            {busy ? "Fetching..." : "Preview import"}
          </button>
          <label className="upload-label">
            <span>or upload a .md / .zip / .tar.gz</span>
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
            <p>
              <strong>This source holds {choices.length} skills.</strong> Pick the one
              to stage — nothing is written until you confirm its preview.
            </p>
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
                      {choice.description || choice.path || "no description"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="panel-actions">
              <button disabled={busy} onClick={cancelPreview} type="button">
                Cancel
              </button>
            </div>
          </div>
        ) : null}

        {preview ? (
          <div className="import-preview">
            <p>
              <strong>{preview.name}</strong> — {preview.description || "no description"}
            </p>
            <p className="muted">
              {preview.source_kind}
              {preview.source_ref ? ` @ ${preview.source_ref}` : ""} ·{" "}
              {preview.files.length} file(s) · {formatBytes(preview.total_bytes)}
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
              <span>Replace the staged skill if it already exists (discards local edits)</span>
            </label>
            <div className="panel-actions">
              <button className="primary" disabled={busy} onClick={handleConfirmImport} type="button">
                {busy ? "Importing..." : "Import"}
              </button>
              <button disabled={busy} onClick={cancelPreview} type="button">
                Cancel
              </button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{editing ? `Edit staged skill: ${editing}` : "New staged skill"}</h2>
          {editing ? (
            <button onClick={resetForm} type="button">
              New instead
            </button>
          ) : null}
        </div>
        <div className="form-grid">
          <label>
            <span>Name (kebab-case)</span>
            <input
              disabled={editing !== null}
              onChange={(event) => setName(event.target.value)}
              placeholder="my-new-skill"
              value={name}
            />
          </label>
          <label>
            <span>Description</span>
            <input
              onChange={(event) => setDescription(event.target.value)}
              placeholder="When should an agent reach for this skill?"
              value={description}
            />
          </label>
          {editing ? null : (
            <label className="full">
              <span>SKILL.md content</span>
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
                  placeholder="scripts/run.sh"
                  value={newFilePath}
                />
                <button
                  disabled={busy || newFilePath.trim().length === 0}
                  onClick={handleAddFile}
                  type="button"
                >
                  Add file
                </button>
              </div>
            </div>
            <div className="file-editor">
              {activeFile && activeFile.encoding === "base64" ? (
                <p className="muted">
                  <code>{activeFile.path}</code> is a binary asset ({formatBytes(activeFile.size)});
                  it is stored and materialized but cannot be edited here.
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
                {busy ? "Saving..." : `Save ${activePath || "file"}`}
              </button>
              <button disabled={busy} onClick={handleSave} type="button">
                Save description
              </button>
            </>
          ) : (
            <button
              className="primary"
              disabled={busy || name.trim().length === 0}
              onClick={handleSave}
              type="button"
            >
              {busy ? "Saving..." : "Create staged skill"}
            </button>
          )}
        </div>
        {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
        {notice ? <p className="muted">{notice}</p> : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Staged skills</h2>
          <span className="muted">{staged.length} total</span>
        </div>
        {staged.length === 0 ? (
          <EmptyState
            description="Import one above, then enable it as Experimental in a run to A/B it."
            title="No staged skills yet."
          />
        ) : (
          <div className="table table--staged">
            {staged.map((skill) => (
              <div className="table-row harness-row" key={skill.name}>
                <span className="harness-name">
                  <strong>{skill.name}</strong>
                  <small>{skill.description || "no description"}</small>
                  {skill.source_url ? (
                    <small className="muted">
                      {skill.source_kind}: {skill.source_url}
                      {skill.source_ref ? ` @ ${skill.source_ref}` : ""}
                    </small>
                  ) : null}
                </span>
                <span className={STATUS_BADGE[skill.status]}>{skill.status}</span>
                <span className="row-actions">
                  <button disabled={busy} onClick={() => handleEdit(skill)} type="button">
                    Edit
                  </button>
                  {skill.status !== "promoted" ? (
                    <button disabled={busy} onClick={() => handlePromote(skill)} type="button">
                      Promote
                    </button>
                  ) : null}
                  <button
                    disabled={busy}
                    onClick={() =>
                      handleStatus(skill, skill.status === "archived" ? "draft" : "archived")
                    }
                    type="button"
                  >
                    {skill.status === "archived" ? "Unarchive" : "Archive"}
                  </button>
                  <button disabled={busy} onClick={() => handleDelete(skill)} type="button">
                    Delete
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Governed catalog</h2>
          <span className="muted">{governed.length} skills (read-only)</span>
        </div>
        <div className="table">
          {governed.map((skill) => (
            <div className="table-row harness-row" key={skill.name}>
              <span className="harness-name">
                <strong>{skill.name}</strong>
                <small>{skill.description || "no description"}</small>
              </span>
              <span className="badge neutral">{skill.kind}</span>
              <span className="muted">edit via .github/skills + make sync-skills</span>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}
