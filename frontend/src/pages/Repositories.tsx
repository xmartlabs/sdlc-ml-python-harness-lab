import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { ReadinessReportView } from "../components/ReadinessReportView";
import { Spinner } from "../components/Spinner";
import { defineMessages, useMessages } from "../i18n";
import type {
  CaseCandidate,
  CaseCandidates,
  ReadinessReport,
  TargetRecord,
  TargetResolveResult
} from "../types";

/** Multi-line textareas hold one shell command per line. */
function toLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

type FormState = {
  target_id: string;
  name: string;
  repository_url: string;
  description: string;
  default_ref: string;
  setup: string;
  validation: string;
  language: string;
  team: string;
};

const EMPTY_FORM: FormState = {
  target_id: "",
  name: "",
  repository_url: "",
  description: "",
  default_ref: "main",
  setup: "",
  validation: "",
  language: "",
  team: ""
};

const MESSAGES = defineMessages({
  en: {
    errors: {
      loadRepositories: "Unable to load repositories",
      saveRepository: "Unable to save repository",
      inspectRepository: "Unable to inspect the repository",
      archiveRepository: "Unable to archive repository",
      couldNotResolve: "Could not resolve",
      readRepository: "Unable to read the repository",
      readHistory: "Unable to read the repository's history",
      adoptInferred: "Unable to adopt the inferred commands"
    },
    header: {
      title: "Repositories",
      description:
        "The repositories a case can be run against. A case is paired with a " +
        "repository at the run, so the same case can be evaluated across several " +
        "of them.",
      crossRepoLink: "Test the harness across these repositories",
      crossRepoNeedsMore: (count: 0 | 1) =>
        `Cross-repo evaluation needs at least two active repositories to vary — ${
          count === 0 ? "none" : "one"
        } registered so far.`
    },
    helpPanel: {
      title: "How a repository is pinned",
      body: (
        <>
          The checkout lives under <code>data/checkouts/</code>, because that is the
          only folder a run container can see. You put it there: clone it yourself
          with the command this page shows, attach a clone that is already in that
          folder under any name, or have the lab copy a repository you already have
          on this machine. The lab never fetches a remote on your behalf. It then{" "}
          <strong>pins the commit</strong> that checkout is on, and every run,
          readiness read and derived case works from that pinned commit until you
          re-pin it. Re-running later is the same experiment, not whatever was
          pushed since.
        </>
      ),
      bodyNote: (
        <>
          <strong>Setup</strong> runs before the agent (for example{" "}
          <code>uv sync</code>). <strong>Validation</strong> are the repository's own
          gates, and they replace the case's when a run has a target — the repo
          knows how it is tested better than a portable case does.
        </>
      )
    },
    form: {
      editTitle: (id: string) => `Edit ${id}`,
      addTitle: "Add a repository",
      id: "Id",
      name: "Name",
      repositoryUrl: "Repository URL",
      defaultRef: "Default ref",
      language: "Language",
      team: "Team",
      setupLabel: "Setup commands — one per line, run before the agent",
      validationLabel: "Validation commands — one per line, the repo's own gates",
      saving: "Saving…",
      saveChanges: "Save changes",
      addRepository: "Add repository",
      inspecting: "Inspecting…",
      inspectRepository: "Inspect repository",
      cancel: "Cancel",
      inspectHint:
        "Inspect reads the repository before you save it — it checks the runtime " +
        "is one the runner image can build (Python or Node), and that your setup " +
        "and validation commands name a toolchain the repository actually has. It " +
        "only warns; you can still add the repository.",
      incompatible: "This repository is likely not to work in the lab",
      compatible: "Runtime looks compatible",
      dismiss: "Dismiss"
    },
    table: {
      title: "Registered repositories",
      showArchived: "Show archived",
      noneTitle: "No target repositories yet",
      noneDescription: "Add one above to run a case against an external repository.",
      colId: "Id",
      colRepository: "Repository",
      colDefaultRef: "Default ref",
      colResolvesTo: "Resolves to",
      colGates: "Gates",
      archived: "archived",
      checking: "checking…",
      check: "Check",
      gatesCommands: (count: number) => `${count} command(s)`,
      caseDefault: "case default",
      open: "Open",
      edit: "Edit",
      reading: "Reading…",
      readiness: "Readiness",
      deriveCases: "Derive cases",
      archive: "Archive"
    },
    readinessCard: {
      title: (targetId: string) => `Readiness — ${targetId}`,
      ready: "Ready to run",
      notReady: "Not runnable yet",
      narrowClaim:
        "\"Ready\" is a narrow claim: a run against this repository would execute " +
        "and be scored. It says nothing about how good the repository's own AI " +
        "governance is.",
      close: "Close"
    },
    derived: {
      title: (targetId: string, count: number) => `Candidate cases from ${targetId} (${count})`,
      intro: (
        <>
          Mined from commits that changed source <strong>and</strong> its tests
          together — tasks this team performs and already knows how to verify.
          Nothing is saved: read them, keep the ones the team would recognise as
          their work, and create those as cases.
        </>
      ),
      note: (
        <>
          Each candidate carries the commit it came from, which is what lets you
          show later that the case predates the results instead of having been
          written to fit them. Freeze the case and its rubric{" "}
          <strong>before</strong> running either harness condition.
        </>
      ),
      noneTitle: "No candidates found",
      noneDescription:
        "No commit in the scanned history changed source and tests together. " +
        "Widen the scan, or the repository may not keep tests in recognisable paths.",
      candidateMeta: (commit: string, files: number, tests: number) =>
        `${commit} · ${files} file(s) · ${tests} test(s)`,
      suggestedId: "Suggested case id",
      suggestedPrompt: "Suggested prompt — edit before using",
      createHint: (
        <>
          Create it from this repository&apos;s workbench (Open → Cases), so it
          carries <code>origin_target_id</code>.
        </>
      )
    }
  },
  es: {
    errors: {
      loadRepositories: "No se pudieron cargar los repositorios",
      saveRepository: "No se pudo guardar el repositorio",
      inspectRepository: "No se pudo inspeccionar el repositorio",
      archiveRepository: "No se pudo archivar el repositorio",
      couldNotResolve: "No se pudo resolver",
      readRepository: "No se pudo leer el repositorio",
      readHistory: "No se pudo leer el historial del repositorio",
      adoptInferred: "No se pudieron adoptar los comandos inferidos"
    },
    header: {
      title: "Repositorios",
      description:
        "Los repositorios contra los que se puede correr un caso. Un caso se " +
        "empareja con un repositorio al lanzar la corrida, así que el mismo caso " +
        "se puede evaluar en varios de ellos.",
      crossRepoLink: "Probá el arnés entre estos repositorios",
      crossRepoNeedsMore: (count: 0 | 1) =>
        `La evaluación entre repositorios necesita al menos dos repositorios ` +
        `activos para variar — hay ${count === 0 ? "ninguno" : "uno"} registrado ` +
        `hasta ahora.`
    },
    helpPanel: {
      title: "Cómo se fija un repositorio",
      body: (
        <>
          El checkout vive en <code>data/checkouts/</code>, porque es la única
          carpeta que un contenedor de corrida puede ver. Vos lo ponés ahí: cloná el
          repositorio con el comando que muestra esta página, adjuntá un clon que ya
          esté en esa carpeta con cualquier nombre, o dejá que el lab copie un
          repositorio que ya tenés en esta máquina. El lab nunca hace fetch de un
          remoto por su cuenta. Después <strong>fija el commit</strong> en el que
          está ese checkout, y cada corrida, lectura de estado y caso derivado
          trabaja desde ese commit fijado hasta que lo volvás a fijar. Correr de
          nuevo más tarde es el mismo experimento, no lo que se haya subido desde
          entonces.
        </>
      ),
      bodyNote: (
        <>
          <strong>Setup</strong> corre antes del agente (por ejemplo{" "}
          <code>uv sync</code>). <strong>Validation</strong> son los propios gates
          del repositorio, y reemplazan a los del caso cuando una corrida tiene un
          repositorio objetivo — el repo sabe cómo se lo testea mejor que un caso
          portable.
        </>
      )
    },
    form: {
      editTitle: (id: string) => `Editar ${id}`,
      addTitle: "Agregar un repositorio",
      id: "Id",
      name: "Nombre",
      repositoryUrl: "URL del repositorio",
      defaultRef: "Ref por defecto",
      language: "Lenguaje",
      team: "Equipo",
      setupLabel: "Comandos de setup — uno por línea, corren antes del agente",
      validationLabel: "Comandos de validación — uno por línea, los gates del repo",
      saving: "Guardando…",
      saveChanges: "Guardar cambios",
      addRepository: "Agregar repositorio",
      inspecting: "Inspeccionando…",
      inspectRepository: "Inspeccionar repositorio",
      cancel: "Cancelar",
      inspectHint:
        "Inspeccionar lee el repositorio antes de guardarlo — chequea que el " +
        "runtime sea uno que la imagen del runner pueda construir (Python o Node), " +
        "y que tus comandos de setup y validación nombren un toolchain que el " +
        "repositorio realmente tenga. Solo avisa; igual podés agregar el " +
        "repositorio.",
      incompatible: "Es probable que este repositorio no funcione en el lab",
      compatible: "El runtime parece compatible",
      dismiss: "Descartar"
    },
    table: {
      title: "Repositorios registrados",
      showArchived: "Mostrar archivados",
      noneTitle: "Todavía no hay repositorios objetivo",
      noneDescription:
        "Agregá uno arriba para correr un caso contra un repositorio externo.",
      colId: "Id",
      colRepository: "Repositorio",
      colDefaultRef: "Ref por defecto",
      colResolvesTo: "Resuelve a",
      colGates: "Gates",
      archived: "archivado",
      checking: "chequeando…",
      check: "Chequear",
      gatesCommands: (count: number) => `${count} comando(s)`,
      caseDefault: "por defecto del caso",
      open: "Abrir",
      edit: "Editar",
      reading: "Leyendo…",
      readiness: "Estado",
      deriveCases: "Derivar casos",
      archive: "Archivar"
    },
    readinessCard: {
      title: (targetId: string) => `Estado — ${targetId}`,
      ready: "Listo para correr",
      notReady: "Todavía no se puede correr",
      narrowClaim:
        "\"Listo\" es una afirmación acotada: una corrida contra este repositorio " +
        "se ejecutaría y se puntuaría. No dice nada sobre qué tan buena es la " +
        "propia gobernanza de IA del repositorio.",
      close: "Cerrar"
    },
    derived: {
      title: (targetId: string, count: number) => `Casos candidatos de ${targetId} (${count})`,
      intro: (
        <>
          Extraídos de commits que cambiaron código fuente <strong>y</strong> sus
          tests juntos — tareas que este equipo hace y ya sabe cómo verificar. No se
          guarda nada: leelos, quedate con los que el equipo reconocería como su
          trabajo, y creá esos como casos.
        </>
      ),
      note: (
        <>
          Cada candidato lleva el commit del que vino, que es lo que te permite
          mostrar después que el caso es anterior a los resultados en vez de haber
          sido escrito para ajustarse a ellos. Congelá el caso y su rúbrica{" "}
          <strong>antes</strong> de correr cualquiera de las condiciones del arnés.
        </>
      ),
      noneTitle: "No se encontraron candidatos",
      noneDescription:
        "Ningún commit del historial escaneado cambió código fuente y tests " +
        "juntos. Ampliá el escaneo, o puede que el repositorio no guarde los tests " +
        "en rutas reconocibles.",
      candidateMeta: (commit: string, files: number, tests: number) =>
        `${commit} · ${files} archivo(s) · ${tests} test(s)`,
      suggestedId: "Id de caso sugerido",
      suggestedPrompt: "Prompt sugerido — editalo antes de usarlo",
      createHint: (
        <>
          Creálo desde el banco de trabajo de este repositorio (Abrir → Cases), para
          que lleve <code>origin_target_id</code>.
        </>
      )
    }
  }
});

function formFrom(target: TargetRecord): FormState {
  return {
    target_id: target.target_id,
    name: target.name,
    repository_url: target.repository_url,
    description: target.description,
    default_ref: target.default_ref,
    setup: target.setup_commands.join("\n"),
    validation: target.validation_commands.join("\n"),
    language: target.language,
    team: target.team
  };
}

export function Repositories() {
  const m = useMessages(MESSAGES);
  const [targets, setTargets] = useState<TargetRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  // Per-target pin check: what the default ref points at right now.
  const [pins, setPins] = useState<Record<string, TargetResolveResult | string>>({});
  const [checking, setChecking] = useState<string | null>(null);
  // Derived case candidates: proposals mined from the repo's own history.
  const [derived, setDerived] = useState<CaseCandidates | null>(null);
  const [deriving, setDeriving] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  // Read on demand rather than on load: it clones and reads the repository,
  // which is far too expensive to do for every row of a table nobody asked
  // a question of yet.
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  // Pre-creation inspection of the repository the form is describing. Read-only
  // (nothing is saved) and non-blocking: it warns, it does not stop the Add.
  const [inspection, setInspection] = useState<ReadinessReport | null>(null);
  const [inspecting, setInspecting] = useState(false);

  const load = async (archived = showArchived) => {
    setIsLoading(true);
    try {
      setTargets(await api.listTargets(archived));
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : m.errors.loadRepositories);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load(showArchived);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showArchived]);

  // Archived repositories are still listed when the box is ticked, but a mode
  // that varies the repository can only vary the ones that still run.
  const activeTargets = targets.filter((target) => target.status !== "archived");

  const handleSubmit = async () => {
    setIsSaving(true);
    setError(null);
    try {
      const payload = {
        target_id: form.target_id.trim(),
        name: form.name.trim() || form.target_id.trim(),
        repository_url: form.repository_url.trim(),
        description: form.description,
        default_ref: form.default_ref.trim() || "main",
        setup_commands: toLines(form.setup),
        validation_commands: toLines(form.validation),
        language: form.language.trim(),
        team: form.team.trim()
      };
      if (editingId) {
        await api.updateTarget(editingId, payload);
      } else {
        await api.createTarget(payload);
      }
      setForm(EMPTY_FORM);
      setEditingId(null);
      setInspection(null);
      await load();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : m.errors.saveRepository);
    } finally {
      setIsSaving(false);
    }
  };

  const handleInspect = async () => {
    setInspecting(true);
    setError(null);
    setInspection(null);
    try {
      setInspection(
        await api.inspectTarget({
          repository_url: form.repository_url.trim(),
          default_ref: form.default_ref.trim() || "main",
          setup_commands: toLines(form.setup),
          validation_commands: toLines(form.validation)
        })
      );
    } catch (inspectError) {
      setError(
        inspectError instanceof Error ? inspectError.message : m.errors.inspectRepository
      );
    } finally {
      setInspecting(false);
    }
  };

  const handleArchive = async (targetId: string) => {
    setError(null);
    try {
      await api.archiveTarget(targetId);
      await load();
    } catch (archiveError) {
      setError(
        archiveError instanceof Error ? archiveError.message : m.errors.archiveRepository
      );
    }
  };

  const handleCheck = async (target: TargetRecord) => {
    setChecking(target.target_id);
    try {
      const resolved = await api.resolveTargetRef(target.target_id, target.default_ref);
      setPins((current) => ({ ...current, [target.target_id]: resolved }));
    } catch (checkError) {
      setPins((current) => ({
        ...current,
        [target.target_id]:
          checkError instanceof Error ? checkError.message : m.errors.couldNotResolve
      }));
    } finally {
      setChecking(null);
    }
  };

  const handleReadiness = async (targetId: string) => {
    setReading(targetId);
    setError(null);
    try {
      setReadiness(await api.getTargetReadiness(targetId));
    } catch (readError) {
      setError(readError instanceof Error ? readError.message : m.errors.readRepository);
    } finally {
      setReading(null);
    }
  };

  const handleDerive = async (target: TargetRecord) => {
    setDeriving(target.target_id);
    setError(null);
    setDerived(null);
    try {
      setDerived(await api.deriveCases(target.target_id));
    } catch (deriveError) {
      setError(deriveError instanceof Error ? deriveError.message : m.errors.readHistory);
    } finally {
      setDeriving(null);
    }
  };

  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <h1>{m.header.title}</h1>
          <p className="muted">{m.header.description}</p>
        </div>
        {/*
          Cross-repo lives here rather than on the home page: it is the one mode
          that cannot be started without the thing this page holds. Offered on
          the front page it was a door that did not open; offered here it appears
          exactly when the second repository does.
        */}
        <div className="page-header-action">
          {activeTargets.length >= 2 ? (
            <Link className="primary" to="/evaluate/new?mode=cross_repo">
              {m.header.crossRepoLink}
            </Link>
          ) : (
            <p className="muted">
              {m.header.crossRepoNeedsMore(activeTargets.length === 0 ? 0 : 1)}
            </p>
          )}
        </div>
      </header>

      <HelpPanel title={m.helpPanel.title}>
        <p>{m.helpPanel.body}</p>
        <p className="muted">{m.helpPanel.bodyNote}</p>
      </HelpPanel>

      {error !== null && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="card">
        <h2>{editingId ? m.form.editTitle(editingId) : m.form.addTitle}</h2>
        <div className="form-grid">
          <label>
            <span>{m.form.id}</span>
            <input
              disabled={editingId !== null}
              onChange={(event) =>
                setForm({ ...form, target_id: event.target.value })
              }
              placeholder="company-payments"
              value={form.target_id}
            />
          </label>
          <label>
            <span>{m.form.name}</span>
            <input
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Payments API"
              value={form.name}
            />
          </label>
          <label>
            <span>{m.form.repositoryUrl}</span>
            <input
              onChange={(event) =>
                setForm({ ...form, repository_url: event.target.value })
              }
              placeholder="https://github.com/company/payments-api.git"
              value={form.repository_url}
            />
          </label>
          <label>
            <span>{m.form.defaultRef}</span>
            <input
              onChange={(event) =>
                setForm({ ...form, default_ref: event.target.value })
              }
              placeholder="main"
              value={form.default_ref}
            />
          </label>
          <label>
            <span>{m.form.language}</span>
            <input
              onChange={(event) => setForm({ ...form, language: event.target.value })}
              placeholder="python"
              value={form.language}
            />
          </label>
          <label>
            <span>{m.form.team}</span>
            <input
              onChange={(event) => setForm({ ...form, team: event.target.value })}
              placeholder="payments"
              value={form.team}
            />
          </label>
        </div>
        <label className="stacked-field">
          <span>{m.form.setupLabel}</span>
          <textarea
            onChange={(event) => setForm({ ...form, setup: event.target.value })}
            placeholder={"uv sync"}
            rows={3}
            value={form.setup}
          />
        </label>
        <label className="stacked-field">
          <span>{m.form.validationLabel}</span>
          <textarea
            onChange={(event) => setForm({ ...form, validation: event.target.value })}
            placeholder={"make lint\nmake test"}
            rows={3}
            value={form.validation}
          />
        </label>
        <div className="row gap">
          <button
            className="primary"
            disabled={
              isSaving ||
              form.target_id.trim() === "" ||
              form.repository_url.trim() === ""
            }
            onClick={() => void handleSubmit()}
            type="button"
          >
            {isSaving ? m.form.saving : editingId ? m.form.saveChanges : m.form.addRepository}
          </button>
          <button
            disabled={inspecting || form.repository_url.trim() === ""}
            onClick={() => void handleInspect()}
            type="button"
          >
            {inspecting ? m.form.inspecting : m.form.inspectRepository}
          </button>
          {editingId !== null && (
            <button
              onClick={() => {
                setEditingId(null);
                setForm(EMPTY_FORM);
                setInspection(null);
              }}
              type="button"
            >
              {m.form.cancel}
            </button>
          )}
        </div>
        <p className="muted small">{m.form.inspectHint}</p>

        {inspection !== null &&
          (() => {
            const runtime = inspection.checks.find(
              (check) => check.label === "Runtime compatibility"
            );
            const bad = runtime !== undefined && runtime.status !== "ok";
            return (
              <div className="stack">
                {runtime !== undefined && (
                  <div className={`callout ${bad ? "callout--warning" : "callout--ok"}`}>
                    <div>
                      <strong>{bad ? m.form.incompatible : m.form.compatible}</strong>
                      <p>{runtime.detail}</p>
                      {runtime.items.length > 0 && (
                        <ul className="check-items">
                          {runtime.items.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>
                )}
                <ReadinessReportView report={inspection} />
                <div className="row">
                  <button onClick={() => setInspection(null)} type="button">
                    {m.form.dismiss}
                  </button>
                </div>
              </div>
            );
          })()}
      </div>

      <div className="card">
        <div className="row space-between">
          <h2>{m.table.title}</h2>
          <label className="row gap">
            <input
              checked={showArchived}
              onChange={(event) => setShowArchived(event.target.checked)}
              type="checkbox"
            />
            <span>{m.table.showArchived}</span>
          </label>
        </div>

        {isLoading ? (
          <Spinner />
        ) : targets.length === 0 ? (
          <EmptyState description={m.table.noneDescription} title={m.table.noneTitle} />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.table.colId}</th>
                <th>{m.table.colRepository}</th>
                <th>{m.table.colDefaultRef}</th>
                <th>{m.table.colResolvesTo}</th>
                <th>{m.table.colGates}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {targets.map((target) => {
                const pin = pins[target.target_id];
                return (
                  <tr key={target.target_id}>
                    <td>
                      <strong>{target.target_id}</strong>
                      {target.status === "archived" && (
                        <span className="badge"> {m.table.archived}</span>
                      )}
                      <div className="muted small">{target.name}</div>
                    </td>
                    <td className="mono small">{target.repository_url}</td>
                    <td className="mono small">{target.default_ref}</td>
                    <td className="mono small">
                      {checking === target.target_id ? (
                        m.table.checking
                      ) : pin === undefined ? (
                        <button onClick={() => void handleCheck(target)} type="button">
                          {m.table.check}
                        </button>
                      ) : typeof pin === "string" ? (
                        <span className="error-text">{pin}</span>
                      ) : (
                        <span title={pin.commit}>{pin.commit.slice(0, 10)}</span>
                      )}
                    </td>
                    <td className="small">
                      {target.validation_commands.length > 0
                        ? m.table.gatesCommands(target.validation_commands.length)
                        : m.table.caseDefault}
                    </td>
                    <td className="row gap">
                      {/* The workbench, where this repository's checkout, cases
                          and experiments accumulate. */}
                      <Link
                        className="button-link"
                        to={`/evidence/repositories/${target.target_id}`}
                      >
                        {m.table.open}
                      </Link>
                      <button
                        onClick={() => {
                          setEditingId(target.target_id);
                          setForm(formFrom(target));
                        }}
                        type="button"
                      >
                        {m.table.edit}
                      </button>
                      <button
                        disabled={reading !== null}
                        onClick={() => void handleReadiness(target.target_id)}
                        type="button"
                      >
                        {reading === target.target_id ? m.table.reading : m.table.readiness}
                      </button>
                      <button
                        disabled={deriving !== null}
                        onClick={() => void handleDerive(target)}
                        type="button"
                      >
                        {deriving === target.target_id ? m.table.reading : m.table.deriveCases}
                      </button>
                      {target.status === "active" && (
                        <button
                          onClick={() => void handleArchive(target.target_id)}
                          type="button"
                        >
                          {m.table.archive}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {readiness !== null && (
        <div className="card">
          <div className="row space-between">
            <h2>{m.readinessCard.title(readiness.target_id)}</h2>
            <span className={`badge ${readiness.ready ? "ok" : "warn"}`}>
              {readiness.ready ? m.readinessCard.ready : m.readinessCard.notReady}
            </span>
          </div>
          <p className="muted small">{m.readinessCard.narrowClaim}</p>
          <ReadinessReportView
            adopting={reading !== null}
            onAdopt={(payload) => {
              void (async () => {
                setReading(readiness.target_id);
                try {
                  await api.updateTarget(readiness.target_id, payload);
                  setTargets(await api.listTargets());
                  setReadiness(await api.getTargetReadiness(readiness.target_id));
                } catch (adoptError) {
                  setError(
                    adoptError instanceof Error
                      ? adoptError.message
                      : m.errors.adoptInferred
                  );
                } finally {
                  setReading(null);
                }
              })();
            }}
            report={readiness}
          />
          <button onClick={() => setReadiness(null)} type="button">
            {m.readinessCard.close}
          </button>
        </div>
      )}

      {derived !== null && (
        <div className="card">
          <h2>{m.derived.title(derived.target_id, derived.candidates.length)}</h2>
          <p className="muted">{m.derived.intro}</p>
          <p className="muted small">{m.derived.note}</p>
          {derived.candidates.length === 0 ? (
            <EmptyState
              description={m.derived.noneDescription}
              title={m.derived.noneTitle}
            />
          ) : (
            <ul className="candidate-list">
              {derived.candidates.map((candidate: CaseCandidate) => (
                <li key={candidate.commit}>
                  <button
                    className="skills-toggle"
                    onClick={() =>
                      setExpanded(
                        expanded === candidate.commit ? null : candidate.commit
                      )
                    }
                    type="button"
                  >
                    {expanded === candidate.commit ? "▾" : "▸"} {candidate.subject}
                    <span className="muted small">
                      {" "}
                      {m.derived.candidateMeta(
                        candidate.commit.slice(0, 7),
                        candidate.source_files.length,
                        candidate.test_files.length
                      )}
                    </span>
                  </button>
                  {expanded === candidate.commit && (
                    <div className="stack">
                      <label className="stacked-field">
                        <span>{m.derived.suggestedId}</span>
                        <input readOnly value={candidate.suggested_case_id} />
                      </label>
                      <label className="stacked-field">
                        <span>{m.derived.suggestedPrompt}</span>
                        <textarea
                          readOnly
                          rows={10}
                          value={candidate.suggested_prompt}
                        />
                      </label>
                      <p className="muted small">{m.derived.createHint}</p>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
