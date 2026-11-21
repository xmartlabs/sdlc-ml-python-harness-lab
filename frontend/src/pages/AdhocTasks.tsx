import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { FolderPicker } from "../components/FolderPicker";
import { Spinner } from "../components/Spinner";
import { IconChecklist, IconGitBranch } from "../components/icons";
import { formatRelative } from "../dates";
import { defineMessages, useMessages } from "../i18n";
import type { AdhocSource, AdhocTaskSummary, LocalSourceListing } from "../types";

/**
 * Ad hoc tasks (ADR-0095): run the lab against a real folder on this machine.
 *
 * The list leads with what already exists; a new task starts from the same
 * folder picker the audit flow uses, then asks for a prompt and, optionally,
 * setup/verification commands.
 */

const MESSAGES = defineMessages({
  en: {
    title: "Ad hoc tasks",
    lede: "Point the lab at a folder on this machine, write a prompt, run it, and apply what you like.",
    newTask: "New task",
    cancel: "Cancel",
    list: {
      title: "Tasks",
      colTitle: "Task",
      colFolder: "Folder",
      colBranch: "Branch",
      colRuns: "Runs",
      colLast: "Last run",
      colApplied: "Applied",
      none: "none",
      applied: "applied",
      emptyTitle: "No ad hoc tasks yet.",
      emptyBody: "Create one below: pick a folder, write what you want done, and run it."
    },
    form: {
      step1: "1. Pick a folder",
      step1Hint: "Click a folder to open it, and press the button on the repository you want.",
      disabledTitle: "Local folders are not shared with the lab.",
      disabledBody: (
        <>
          Set <code>HARNESS_LAB_LOCAL_SOURCES_DIR</code> in <code>.env</code> to the
          directory that holds your repositories, restart the lab, and share that
          same folder in Docker Desktop (Settings → Resources → File sharing).
        </>
      ),
      useThisFolder: "Use this folder",
      usingFolder: "Loading…",
      notARepo: "This folder is not a git repository. Open one of its subfolders, or pick another.",
      dirtyTitle: (count: number) => `${count} uncommitted ${count === 1 ? "change" : "changes"}: the agent will not see them.`,
      dirtyBody: "Commit or stash first if they matter — the task pins the folder's current commit.",
      andMore: (n: number) => `… and ${n} more.`,
      step2: "2. What do you want done?",
      titleLabel: "Title (optional)",
      titlePlaceholder: "A short name for this task",
      promptLabel: "Prompt",
      promptPlaceholder: "Describe the one thing you want changed…",
      promptTips:
        "Tips: one thing per task, state how you will know it is done, and name files if you already know them.",
      advanced: "Advanced: setup and verification commands",
      setupLabel: "Setup commands (one per line)",
      validationLabel: "Verification commands (one per line)",
      create: "Create task",
      creating: "Creating…",
      branch: (name: string) => `on ${name}`,
      head: (sha: string) => `HEAD ${sha}`
    },
    errors: {
      load: "Could not load the task list.",
      browse: "Could not read that folder.",
      noPrompt: "Write a prompt before creating the task.",
      create: "Could not create the task."
    }
  },
  es: {
    title: "Tareas ad hoc",
    lede: "Apuntá el lab a una carpeta de esta máquina, escribí un prompt, corrélo y aplicá lo que te sirva.",
    newTask: "Nueva tarea",
    cancel: "Cancelar",
    list: {
      title: "Tareas",
      colTitle: "Tarea",
      colFolder: "Carpeta",
      colBranch: "Rama",
      colRuns: "Corridas",
      colLast: "Última corrida",
      colApplied: "Aplicada",
      none: "ninguna",
      applied: "aplicada",
      emptyTitle: "Todavía no hay tareas ad hoc.",
      emptyBody: "Creá una abajo: elegí una carpeta, escribí qué querés que se haga, y correla."
    },
    form: {
      step1: "1. Elegí una carpeta",
      step1Hint: "Hacé clic en una carpeta para abrirla, y presioná el botón en el repositorio que querés.",
      disabledTitle: "Las carpetas locales no están compartidas con el lab.",
      disabledBody: (
        <>
          Configurá <code>HARNESS_LAB_LOCAL_SOURCES_DIR</code> en <code>.env</code> con el
          directorio que contiene tus repositorios, reiniciá el lab, y compartí esa
          misma carpeta en Docker Desktop (Settings → Resources → File sharing).
        </>
      ),
      useThisFolder: "Usar esta carpeta",
      usingFolder: "Cargando…",
      notARepo: "Esta carpeta no es un repositorio git. Abrí una de sus subcarpetas, o elegí otra.",
      dirtyTitle: (count: number) => `${count} cambio${count === 1 ? "" : "s"} sin commitear: el agente no los va a ver.`,
      dirtyBody: "Commiteá o guardalos en un stash primero si importan — la tarea fija el commit actual de la carpeta.",
      andMore: (n: number) => `… y ${n} más.`,
      step2: "2. ¿Qué querés que se haga?",
      titleLabel: "Título (opcional)",
      titlePlaceholder: "Un nombre corto para esta tarea",
      promptLabel: "Prompt",
      promptPlaceholder: "Describí lo único que querés cambiar…",
      promptTips:
        "Tips: una sola cosa por tarea, decí cómo vas a saber que terminó, y nombrá archivos si ya los conocés.",
      advanced: "Avanzado: comandos de setup y verificación",
      setupLabel: "Comandos de setup (uno por línea)",
      validationLabel: "Comandos de verificación (uno por línea)",
      create: "Crear tarea",
      creating: "Creando…",
      branch: (name: string) => `en ${name}`,
      head: (sha: string) => `HEAD ${sha}`
    },
    errors: {
      load: "No se pudo cargar la lista de tareas.",
      browse: "No se pudo leer esa carpeta.",
      noPrompt: "Escribí un prompt antes de crear la tarea.",
      create: "No se pudo crear la tarea."
    }
  }
});

function linesOf(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function AdhocTasks() {
  const m = useMessages(MESSAGES);
  const navigate = useNavigate();

  const [tasks, setTasks] = useState<AdhocTaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [listing, setListing] = useState<LocalSourceListing | null>(null);
  const [source, setSource] = useState<AdhocSource | null>(null);
  const [busy, setBusy] = useState("");

  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [setupText, setSetupText] = useState("");
  const [validationText, setValidationText] = useState("");

  useEffect(() => {
    api
      .listAdhocTasks()
      .then(setTasks)
      .catch(() => setError(m.errors.load))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!showForm || listing) {
      return;
    }
    api
      .browseLocalSources("")
      .then(setListing)
      .catch(() => setError(m.errors.browse));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showForm]);

  const browseLocal = (path: string) => {
    setBusy("browse");
    setError(null);
    api
      .browseLocalSources(path)
      .then(setListing)
      .catch(() => setError(m.errors.browse))
      .finally(() => setBusy(""));
  };

  const pickFolder = (path: string) => {
    setBusy("pick");
    setError(null);
    api
      .getAdhocSource(path)
      .then(setSource)
      .catch(() => setError(m.errors.browse))
      .finally(() => setBusy(""));
  };

  const create = async () => {
    if (!source) return;
    if (!prompt.trim()) {
      setError(m.errors.noPrompt);
      return;
    }
    setBusy("create");
    setError(null);
    try {
      const created = await api.createAdhocTask({
        source: source.path,
        title: title.trim() || undefined,
        prompt: prompt.trim(),
        setup_commands: linesOf(setupText),
        validation_commands: linesOf(validationText)
      });
      navigate(`/adhoc/${created.task_id}`);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : m.errors.create);
    } finally {
      setBusy("");
    }
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow hub-eyebrow">
            <IconChecklist size={14} />
            {m.title}
          </p>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
        <div className="page-header-action">
          <button
            className="primary"
            onClick={() => setShowForm((value) => !value)}
            type="button"
          >
            {showForm ? m.cancel : m.newTask}
          </button>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {showForm ? (
        <div className="card">
          <h2>{m.form.step1}</h2>
          <p className="muted small">{m.form.step1Hint}</p>

          {listing === null ? (
            <Spinner />
          ) : listing.enabled ? (
            <FolderPicker
              browsing={busy === "browse"}
              disabled={busy !== ""}
              listing={listing}
              onBrowse={browseLocal}
              onUse={pickFolder}
              useHint=""
              useLabel={m.form.useThisFolder}
              working={busy === "pick"}
              workingLabel={m.form.usingFolder}
            />
          ) : (
            <div className="callout callout--warning">
              <p>
                <strong>{m.form.disabledTitle}</strong> {m.form.disabledBody}
              </p>
            </div>
          )}

          {source ? (
            source.is_repo ? (
              <div className="card subtle" style={{ marginTop: "var(--space-3)" }}>
                <p className="mono small">
                  <IconGitBranch size={14} /> {source.host_path} {m.form.branch(source.branch)} ·{" "}
                  {m.form.head(source.head.slice(0, 8))}
                </p>
                {source.dirty ? (
                  <div className="callout callout--warning">
                    <p>
                      <strong>{m.form.dirtyTitle(source.dirty_files.length)}</strong>{" "}
                      {m.form.dirtyBody}
                      <br />
                      {source.dirty_files.slice(0, 10).map((file) => (
                        <code key={file} style={{ display: "block" }}>
                          {file}
                        </code>
                      ))}
                      {source.dirty_files.length > 10
                        ? m.form.andMore(source.dirty_files.length - 10)
                        : null}
                    </p>
                  </div>
                ) : null}

                <h2>{m.form.step2}</h2>
                <label className="field">
                  <span>{m.form.titleLabel}</span>
                  <input
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder={m.form.titlePlaceholder}
                    type="text"
                    value={title}
                  />
                </label>
                <label className="field">
                  <span>{m.form.promptLabel}</span>
                  <textarea
                    onChange={(event) => setPrompt(event.target.value)}
                    placeholder={m.form.promptPlaceholder}
                    rows={6}
                    value={prompt}
                  />
                  <span className="muted small">{m.form.promptTips}</span>
                </label>

                <details>
                  <summary>{m.form.advanced}</summary>
                  <label className="field">
                    <span>{m.form.setupLabel}</span>
                    <textarea
                      onChange={(event) => setSetupText(event.target.value)}
                      rows={3}
                      value={setupText}
                    />
                  </label>
                  <label className="field">
                    <span>{m.form.validationLabel}</span>
                    <textarea
                      onChange={(event) => setValidationText(event.target.value)}
                      rows={3}
                      value={validationText}
                    />
                  </label>
                </details>

                <div className="button-row">
                  <button
                    className="primary"
                    disabled={busy !== "" || !prompt.trim()}
                    onClick={() => void create()}
                    type="button"
                  >
                    {busy === "create" ? m.form.creating : m.form.create}
                  </button>
                </div>
              </div>
            ) : (
              <p className="callout callout--warning">
                <span>{m.form.notARepo}</span>
              </p>
            )
          ) : null}
        </div>
      ) : null}

      <div className="card">
        <h2>{m.list.title}</h2>
        {loading ? (
          <Spinner />
        ) : tasks.length === 0 ? (
          <EmptyState description={m.list.emptyBody} title={m.list.emptyTitle} />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.list.colTitle}</th>
                <th>{m.list.colFolder}</th>
                <th>{m.list.colBranch}</th>
                <th>{m.list.colRuns}</th>
                <th>{m.list.colLast}</th>
                <th>{m.list.colApplied}</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr
                  className="selectable"
                  key={task.task_id}
                  onClick={() => navigate(`/adhoc/${task.task_id}`)}
                  style={{ cursor: "pointer" }}
                >
                  <td>
                    <strong>{task.title || task.task_id}</strong>
                  </td>
                  <td className="mono small">{task.host_path}</td>
                  <td className="mono small">{task.branch}</td>
                  <td>{task.runs}</td>
                  <td>{task.last_run_at ? formatRelative(task.last_run_at) : m.list.none}</td>
                  <td>
                    {task.applied_run_id ? (
                      <span className="badge badge--completed">{m.list.applied}</span>
                    ) : (
                      m.list.none
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
