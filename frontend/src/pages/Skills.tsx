import { useEffect, useState } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import type { HarnessSkill, StagedSkillRecord } from "../types";

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
        setNotice(`Promoted: wrote ${result.written}. ${result.next_steps}`);
        refresh();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Skills</p>
          <h1>Skill catalog & staging</h1>
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
          <li>Create or edit a staged skill below (one SKILL.md, frontmatter + body).</li>
          <li>
            In the New run form, check it under <strong>Experimental</strong> and launch a run;
            launch a second, identical run without it as the baseline.
          </li>
          <li>
            Compare the two runs (validation score, attribution, LLM audit verdicts) — they have
            different <code>condition_hash</code> values, so they are distinct arms.
          </li>
          <li>
            If it earns its keep, <strong>Promote</strong>: the file lands in
            <code> .github/skills-external/</code> and views re-sync. Review and commit manually.
          </li>
        </ol>
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
          <label className="full">
            <span>SKILL.md content</span>
            <textarea
              className="skill-editor"
              onChange={(event) => setContent(event.target.value)}
              value={content}
            />
          </label>
        </div>
        <div className="panel-actions">
          <button
            className="primary"
            disabled={busy || (!editing && name.trim().length === 0)}
            onClick={handleSave}
            type="button"
          >
            {busy ? "Saving..." : editing ? "Save changes" : "Create staged skill"}
          </button>
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
            description="Create one above, then enable it as Experimental in a run to A/B it."
            title="No staged skills yet."
          />
        ) : (
          <div className="table table--staged">
            {staged.map((skill) => (
              <div className="table-row harness-row" key={skill.name}>
                <span className="harness-name">
                  <strong>{skill.name}</strong>
                  <small>{skill.description || "no description"}</small>
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
