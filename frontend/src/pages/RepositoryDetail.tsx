import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { ReadinessReportView } from "../components/ReadinessReportView";
import { Spinner } from "../components/Spinner";
import { formatRunDate } from "../dates";
import { defineMessages, useMessages } from "../i18n";
import type {
  CaseRecord,
  CheckoutState,
  ConditionRecord,
  ExperimentRecord,
  GateCheckRecord,
  ReadinessReport,
  StagedSkillRecord,
  TargetRecord
} from "../types";

type TabKey = "checkout" | "readiness" | "cases" | "experiments" | "report";

const TAB_KEYS: TabKey[] = ["checkout", "readiness", "cases", "experiments", "report"];

/** An experiment read whole, because only the detail endpoint carries arms. */
type ExperimentDetail = ExperimentRecord & { conditions: ConditionRecord[] };

type PresetKey = "" | "skills" | "harness";

/**
 * The harness both arms run under.
 *
 * Neither preset varies it — the harness is what is held fixed while the skill
 * set or the governance source moves — so it is a constant here rather than a
 * fourth field in a form that already asks enough.
 */
const LAUNCH_HARNESS = "opencode";

/** Poll interval for a gate check, in milliseconds. */
const GATE_POLL_MS = 4000;

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 52) || "repository"
  );
}

function isDuplicateId(error: unknown): boolean {
  return (
    (error instanceof ApiError && error.status === 409) ||
    (error instanceof Error && error.message.includes("already exists"))
  );
}

function readTargetRepoId(overrides: Record<string, unknown>): string {
  const value = overrides.target_repo_id;
  return typeof value === "string" ? value : "";
}

const MESSAGES = defineMessages({
  en: {
    tabs: {
      checkout: "Checkout",
      readiness: "Readiness",
      cases: "Cases",
      experiments: "Experiments",
      report: "Report"
    },
    errors: {
      somethingWrong: "Something went wrong",
      loadRepository: "Unable to load this repository",
      findFreeId: "Unable to find a free experiment id."
    },
    loadingRepository: "Reading this repository",
    notFound: {
      title: "Repository not found",
      description: (targetId: string) => `No repository is registered under '${targetId}'.`,
      back: "Back to repositories"
    },
    header: {
      pinnedAt: (commit: ReactNode) => (
        <>Pinned at {commit} — every case, run and report below reads that commit.</>
      ),
      unpinned: "unpinned",
      unpinnedNote: "Nothing here is comparable until this repository is pinned to a commit.",
      allRepositories: "All repositories"
    },
    checkout: {
      title: "Local checkout",
      cloned: "Cloned",
      notCloned: "Not cloned",
      intro:
        "You clone this repository in your own terminal so your credentials apply, " +
        "or hand the lab a clone you already have; either way the lab only ever " +
        "reads it.",
      reading: "Reading the checkout",
      path: "Path",
      attachedNote: "an existing clone, attached to this repository",
      head: "HEAD",
      branch: "Branch",
      pinnedCommit: "Pinned commit",
      notPinned: "not pinned",
      pinnedAtLabel: "Pinned at",
      dirtyTitle: "The working tree has uncommitted changes",
      dirtyBody: (files: number, head: string) => (
        <>
          {files} file(s) differ from {head}. A run reads the pinned commit, not
          your edits, so what you see locally is not what was measured.
        </>
      ),
      staleTitle: "HEAD has moved since the pin",
      staleBody:
        "Re-pinning changes what every future case, run and report reads. Results " +
        "already recorded keep the commit they used.",
      pinning: "Pinning…",
      rePin: "Re-pin to HEAD",
      cloneYourself: "Clone it yourself",
      copy: "Copy",
      copiedNotice: "Clone command copied to the clipboard.",
      pinnedNotice: (commit: string) =>
        `Pinned to ${commit}. Everything measured from now on reads that commit.`
    },
    readinessTab: {
      title: "What this repository already has",
      ready: "Ready to run",
      notReady: "Not runnable yet",
      reading: "Reading the repository",
      adoptedNotice: "Adopted. Runs against this repository now validate with its own commands.",
      gateTitle: "Gate check",
      running: "Running…",
      runGateCheck: "Run gate check",
      gateIntro:
        "It runs the repository's own setup and validation commands at the pinned " +
        "commit with no agent, so a red run afterwards is a fact about the agent " +
        "rather than about the runner image.",
      noGateTitle: "No gate check yet",
      noGateDescription:
        "Run one to find out whether this repository builds and passes on its own.",
      verdict: "Verdict",
      verdictRunning: "Running",
      verdictPassed: "Passed",
      verdictFailed: (phase: string) => `Failed at ${phase}`,
      unnamedPhase: "an unnamed phase",
      verdictNone: "No verdict",
      commit: "Commit",
      lastUpdated: "Last updated",
      waitingForGate: "Waiting for the gate to finish",
      exit: (code: string) => `exit ${code}`,
      passed: "passed",
      failed: "failed"
    },
    casesTab: {
      title: "Case library",
      count: (count: number) => `${count} case(s)`,
      intro: "Cases derived from this repository's own history. They are what every experiment below runs.",
      loading: "Loading cases",
      noneTitle: "No cases from this repository yet",
      noneDescription: "Derive the first ones from the repository's commits in the audit.",
      deriveFromHistory: "Derive cases from history",
      meta: (type: string, steps: string) => `· ${type} · ${steps} step(s)`,
      noValidation: "No validation command, so a run of this case has no objective verdict — it can only be scored by hand."
    },
    experimentsTab: {
      launchTitle: "Launch an experiment",
      launchIntro:
        "Both presets hold the repository, the cases and the model fixed and vary " +
        "one thing about the harness. Everything runs at the pinned commit.",
      presetSkills: "Native vs native + skills",
      presetHarness: "Native vs the lab harness",
      presetSkillsDescription:
        "Both arms use the repository's own governance. Only the treatment arm " +
        "gets the selected skills.",
      presetHarnessDescription:
        "Both arms use the same cases and model. The control keeps the repository's " +
        "own governance; the treatment gets the lab harness injected into the " +
        "workspace.",
      casesSelected: (count: number) => `Cases — ${count} selected`,
      noCasesTitle: "No cases to run",
      noCasesDescription: "An experiment needs at least one case from this repository.",
      addFirstCase: "Add the first case",
      validationCommands: (count: number) => `${count} validation command(s)`,
      skillsSelected: (count: number) => `Skills — ${count} selected`,
      loadingSkills: "Loading skills",
      staged: "staged",
      enforceSkillUse: "Enforce skill use",
      enforceHint:
        "Without it the treatment arm measures whether the skill was available, " +
        "not whether it was used.",
      repetitionsPerArm: "Repetitions per arm",
      executorModelLabel:
        "Executor model — required for opencode; claude/codex fall back to their CLI default",
      cancel: "Cancel",
      launching: "Launching…",
      createAndLaunch: "Create and launch",
      conditionNative: "The repository's own setup, with no extra skills.",
      conditionSkillsWithEnforce: (skills: string) =>
        `The same setup with ${skills} available and asked for.`,
      conditionSkillsNoEnforce: (skills: string) =>
        `The same setup with ${skills} available.`,
      conditionNativeGovernance: "The repository's own governance, with nothing injected.",
      conditionInjected: "The lab harness injected into the same workspace.",
      questionSkills: (skills: string, name: string) =>
        `Does adding ${skills} change outcomes on ${name}?`,
      questionHarness: (name: string) => `Does the lab harness improve on ${name}'s own setup?`,
      experimentNameSkills: (name: string) => `Skills on ${name}`,
      experimentNameHarness: (name: string) => `Harness source on ${name}`,
      runNotes: (targetId: string, commit: string) =>
        `Both arms run against ${targetId} at ${commit}.`,
      queuedNotice: (count: number, experimentId: string) =>
        `Queued ${count} run(s) under '${experimentId}'.`,
      blockingNoPin:
        "This repository has no pinned commit. Pin it under Checkout first — two " +
        "arms run against a moving ref are not the same experiment twice.",
      blockingNoCases: "Select at least one case. An experiment with no cases measures nothing.",
      blockingNoSkills: "Select at least one skill — that is the only thing the two arms differ on.",
      noStagedSkills:
        "No staged skill yet. The lab's own skills are not in this repository's governance, so this preset measures skills you stage: import or create one on Evidence → Skills, then come back.",
      onThisRepoTitle: "Experiments on this repository",
      readingExperiments: "Reading experiments",
      noneRunTitle: "Nothing has been run against this repository yet",
      noneRunDescription: "Launch one with a preset above.",
      experimentMeta: (status: string, arms: number, cases: number, reps: number) =>
        `${status} · ${arms} arm(s) · ${cases} case(s) · ${reps} rep(s)`
    },
    reportTab: {
      title: "Client report",
      assembling: "Assembling…",
      regenerate: "Regenerate",
      generate: "Generate report",
      download: "Download .md",
      intro:
        "Assembled live from the evidence this repository has produced — readiness, " +
        "cases, experiments and signed findings. It never claims what was not measured.",
      noneTitle: "No report yet",
      noneDescription: "Generate one to read what the lab can currently say about this repository.",
      findingsNote: (
        <>
          Findings are recorded separately — see <Link to="/evidence/findings">Findings</Link>.
        </>
      )
    }
  },
  es: {
    tabs: {
      checkout: "Checkout",
      readiness: "Estado",
      cases: "Casos",
      experiments: "Experimentos",
      report: "Reporte"
    },
    errors: {
      somethingWrong: "Algo salió mal",
      loadRepository: "No se pudo cargar este repositorio",
      findFreeId: "No se pudo encontrar un id de experimento libre."
    },
    loadingRepository: "Leyendo este repositorio",
    notFound: {
      title: "Repositorio no encontrado",
      description: (targetId: string) => `No hay ningún repositorio registrado bajo '${targetId}'.`,
      back: "Volver a repositorios"
    },
    header: {
      pinnedAt: (commit: ReactNode) => (
        <>Fijado en {commit} — cada caso, corrida y reporte de abajo lee ese commit.</>
      ),
      unpinned: "sin fijar",
      unpinnedNote: "Nada acá es comparable hasta que este repositorio esté fijado a un commit.",
      allRepositories: "Todos los repositorios"
    },
    checkout: {
      title: "Checkout local",
      cloned: "Clonado",
      notCloned: "No clonado",
      intro:
        "Cloná este repositorio en tu propia terminal para que apliquen tus " +
        "credenciales, o dale al lab un clon que ya tengas; de cualquier forma el " +
        "lab solo lo lee.",
      reading: "Leyendo el checkout",
      path: "Ruta",
      attachedNote: "un clon existente, adjuntado a este repositorio",
      head: "HEAD",
      branch: "Rama",
      pinnedCommit: "Commit fijado",
      notPinned: "no fijado",
      pinnedAtLabel: "Fijado el",
      dirtyTitle: "El árbol de trabajo tiene cambios sin confirmar",
      dirtyBody: (files: number, head: string) => (
        <>
          {files} archivo(s) difieren de {head}. Una corrida lee el commit fijado,
          no tus ediciones, así que lo que ves localmente no es lo que se midió.
        </>
      ),
      staleTitle: "HEAD se movió desde que se fijó",
      staleBody:
        "Volver a fijar cambia lo que lee cada caso, corrida y reporte futuro. Los " +
        "resultados ya registrados mantienen el commit que usaron.",
      pinning: "Fijando…",
      rePin: "Volver a fijar a HEAD",
      cloneYourself: "Clonalo vos mismo",
      copy: "Copiar",
      copiedNotice: "Comando de clonado copiado al portapapeles.",
      pinnedNotice: (commit: string) =>
        `Fijado en ${commit}. Todo lo medido de ahora en más lee ese commit.`
    },
    readinessTab: {
      title: "Lo que este repositorio ya tiene",
      ready: "Listo para correr",
      notReady: "Todavía no se puede correr",
      reading: "Leyendo el repositorio",
      adoptedNotice: "Adoptado. Las corridas contra este repositorio ahora validan con sus propios comandos.",
      gateTitle: "Chequeo del gate",
      running: "Corriendo…",
      runGateCheck: "Correr chequeo del gate",
      gateIntro:
        "Corre los propios comandos de setup y validación del repositorio en el " +
        "commit fijado sin ningún agente, así que una corrida roja después es un " +
        "hecho sobre el agente y no sobre la imagen del runner.",
      noGateTitle: "Todavía no hay chequeo del gate",
      noGateDescription:
        "Corré uno para saber si este repositorio compila y pasa por su cuenta.",
      verdict: "Veredicto",
      verdictRunning: "Corriendo",
      verdictPassed: "Pasó",
      verdictFailed: (phase: string) => `Falló en ${phase}`,
      unnamedPhase: "una fase sin nombre",
      verdictNone: "Sin veredicto",
      commit: "Commit",
      lastUpdated: "Última actualización",
      waitingForGate: "Esperando a que termine el gate",
      exit: (code: string) => `salida ${code}`,
      passed: "pasó",
      failed: "falló"
    },
    casesTab: {
      title: "Biblioteca de casos",
      count: (count: number) => `${count} caso(s)`,
      intro: "Casos derivados del propio historial de este repositorio. Son lo que corre cada experimento de abajo.",
      loading: "Cargando casos",
      noneTitle: "Todavía no hay casos de este repositorio",
      noneDescription: "Derivá los primeros desde los commits del repositorio en la auditoría.",
      deriveFromHistory: "Derivar casos del historial",
      meta: (type: string, steps: string) => `· ${type} · ${steps} paso(s)`,
      noValidation: "No hay comando de validación, así que una corrida de este caso no tiene veredicto objetivo — solo se puede puntuar a mano."
    },
    experimentsTab: {
      launchTitle: "Lanzar un experimento",
      launchIntro:
        "Ambas plantillas mantienen fijos el repositorio, los casos y el modelo, y " +
        "varían una sola cosa del arnés. Todo corre en el commit fijado.",
      presetSkills: "Nativo vs nativo + skills",
      presetHarness: "Nativo vs el arnés del lab",
      presetSkillsDescription:
        "Ambos brazos usan la propia gobernanza del repositorio. Solo el brazo de " +
        "tratamiento recibe las skills seleccionadas.",
      presetHarnessDescription:
        "Ambos brazos usan los mismos casos y modelo. El control mantiene la propia " +
        "gobernanza del repositorio; el tratamiento recibe el arnés del lab " +
        "inyectado en el workspace.",
      casesSelected: (count: number) => `Casos — ${count} seleccionado(s)`,
      noCasesTitle: "No hay casos para correr",
      noCasesDescription: "Un experimento necesita al menos un caso de este repositorio.",
      addFirstCase: "Agregar el primer caso",
      validationCommands: (count: number) => `${count} comando(s) de validación`,
      skillsSelected: (count: number) => `Skills — ${count} seleccionada(s)`,
      loadingSkills: "Cargando skills",
      staged: "en borrador",
      enforceSkillUse: "Exigir el uso de la skill",
      enforceHint:
        "Sin esto, el brazo de tratamiento mide si la skill estaba disponible, no " +
        "si se usó.",
      repetitionsPerArm: "Repeticiones por brazo",
      executorModelLabel:
        "Modelo ejecutor — requerido para opencode; claude/codex recurren a su CLI por defecto",
      cancel: "Cancelar",
      launching: "Lanzando…",
      createAndLaunch: "Crear y lanzar",
      conditionNative: "El propio setup del repositorio, sin skills extra.",
      conditionSkillsWithEnforce: (skills: string) =>
        `El mismo setup con ${skills} disponibles y exigidas.`,
      conditionSkillsNoEnforce: (skills: string) =>
        `El mismo setup con ${skills} disponibles.`,
      conditionNativeGovernance: "La propia gobernanza del repositorio, sin nada inyectado.",
      conditionInjected: "El arnés del lab inyectado en el mismo workspace.",
      questionSkills: (skills: string, name: string) =>
        `¿Agregar ${skills} cambia los resultados en ${name}?`,
      questionHarness: (name: string) => `¿El arnés del lab mejora el propio setup de ${name}?`,
      experimentNameSkills: (name: string) => `Skills en ${name}`,
      experimentNameHarness: (name: string) => `Origen del arnés en ${name}`,
      runNotes: (targetId: string, commit: string) =>
        `Ambos brazos corren contra ${targetId} en ${commit}.`,
      queuedNotice: (count: number, experimentId: string) =>
        `Se encolaron ${count} corrida(s) bajo '${experimentId}'.`,
      blockingNoPin:
        "Este repositorio no tiene un commit fijado. Fijalo en Checkout primero — " +
        "dos brazos corriendo contra una ref que se mueve no son el mismo " +
        "experimento dos veces.",
      blockingNoCases: "Seleccioná al menos un caso. Un experimento sin casos no mide nada.",
      blockingNoSkills: "Seleccioná al menos una skill — es lo único en que difieren los dos brazos.",
      noStagedSkills:
        "Todavía no hay skills en borrador. Las skills propias del lab no están en la gobernanza de este repositorio, así que este preset mide skills en borrador: importá o creá una en Evidencia → Skills y volvé.",
      onThisRepoTitle: "Experimentos en este repositorio",
      readingExperiments: "Leyendo experimentos",
      noneRunTitle: "Todavía no se corrió nada contra este repositorio",
      noneRunDescription: "Lanzá uno con una plantilla de arriba.",
      experimentMeta: (status: string, arms: number, cases: number, reps: number) =>
        `${status} · ${arms} brazo(s) · ${cases} caso(s) · ${reps} repetición(es)`
    },
    reportTab: {
      title: "Reporte para el cliente",
      assembling: "Armando…",
      regenerate: "Regenerar",
      generate: "Generar reporte",
      download: "Descargar .md",
      intro:
        "Armado al vuelo a partir de la evidencia que produjo este repositorio — " +
        "estado, casos, experimentos y hallazgos firmados. Nunca afirma lo que no " +
        "se midió.",
      noneTitle: "Todavía no hay reporte",
      noneDescription: "Generá uno para leer lo que el lab puede decir hoy sobre este repositorio.",
      findingsNote: (
        <>
          Los hallazgos se registran por separado — mirá{" "}
          <Link to="/evidence/findings">Hallazgos</Link>.
        </>
      )
    }
  }
});

export function RepositoryDetail() {
  const { targetId = "" } = useParams();
  const m = useMessages(MESSAGES);

  const [tab, setTab] = useState<TabKey>("checkout");
  const [target, setTarget] = useState<TargetRecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  const [checkout, setCheckout] = useState<CheckoutState | null>(null);
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [gate, setGate] = useState<GateCheckRecord | null>(null);
  const [cases, setCases] = useState<CaseRecord[] | null>(null);
  const [expandedCase, setExpandedCase] = useState("");
  const [experiments, setExperiments] = useState<ExperimentDetail[] | null>(null);
  const [report, setReport] = useState("");

  // Preset form. One at a time: two open launchers on one screen is two ways
  // to answer a question the page asked once.
  const [preset, setPreset] = useState<PresetKey>("");
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>([]);
  const [repetitions, setRepetitions] = useState(3);
  const [executorModel, setExecutorModel] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [enforceInvoke, setEnforceInvoke] = useState(true);
  const [stagedSkills, setStagedSkills] = useState<StagedSkillRecord[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : m.errors.somethingWrong);
    } finally {
      setBusy("");
    }
  };

  // Archived repositories are included: a workbench opened from a bookmark
  // should show the repository, not an empty page, whatever its status.
  useEffect(() => {
    let active = true;
    setIsLoading(true);
    Promise.all([api.listTargets(true), api.getCheckout(targetId)])
      .then(([targets, state]) => {
        if (!active) {
          return;
        }
        setTarget(targets.find((item) => item.target_id === targetId) ?? null);
        setCheckout(state);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : m.errors.loadRepository);
        }
      })
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [targetId]);

  // Each tab reads on first sight rather than on load: readiness clones and
  // reads the repository, and the experiment list costs one request per
  // experiment. Neither is worth paying for a tab nobody opened.
  useEffect(() => {
    if (tab === "readiness" && readiness === null) {
      void run("readiness", async () => {
        setReadiness(await api.getTargetReadiness(targetId));
        setGate(await api.getGateCheck(targetId));
      });
    }
    if (tab === "cases" && cases === null) {
      void run("cases", async () => {
        setCases(await api.listCases(targetId));
      });
    }
    if (tab === "experiments" && experiments === null) {
      void run("experiments", async () => {
        setExperiments(await loadExperiments());
        if (cases === null) {
          setCases(await api.listCases(targetId));
        }
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // A gate check runs in the background, so the page asks again until it ends.
  useEffect(() => {
    if (gate === null || gate.status !== "running") {
      return;
    }
    const timer = window.setInterval(() => {
      void api
        .getGateCheck(targetId)
        .then((next) => setGate(next))
        .catch(() => {
          // A single failed poll is not a failed check — try again next tick.
        });
    }, GATE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [gate, targetId]);

  /**
   * This repository's experiments.
   *
   * `GET /api/experiments` returns no conditions, and the repository lives in a
   * condition's overrides, so each experiment is read once to find out whether
   * it belongs here.
   */
  const loadExperiments = async (): Promise<ExperimentDetail[]> => {
    const listed = await api.listExperiments();
    const details = await Promise.allSettled(
      listed.map((item) => api.getExperiment(item.experiment_id))
    );
    return details
      .flatMap((result) => (result.status === "fulfilled" ? [result.value] : []))
      .filter((detail) =>
        detail.conditions.some(
          (condition) => readTargetRepoId(condition.overrides) === targetId
        )
      );
  };

  const loadSkills = () =>
    run("skills", async () => {
      setStagedSkills(await api.listStagedSkills());
    });

  const rePin = () =>
    run("pin", async () => {
      const state = await api.pinCheckout(targetId);
      setCheckout(state);
      setTarget(
        (current) =>
          current && {
            ...current,
            pinned_commit: state.pinned_commit,
            pinned_at: state.pinned_at
          }
      );
      setNotice(m.checkout.pinnedNotice(state.pinned_commit.slice(0, 10)));
    });

  const runGateCheck = () =>
    run("gate", async () => {
      setGate(await api.startGateCheck(targetId));
    });

  const generateReport = () =>
    run("report", async () => {
      const payload = await api.getAuditReport(targetId);
      setReport(payload.markdown);
    });

  const downloadReport = () => {
    const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${targetId}-report.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const pinnedCommit = target?.pinned_commit ?? checkout?.pinned_commit ?? "";

  const selectedCases = useMemo(
    () => (cases ?? []).filter((item) => selectedCaseIds.includes(item.case_id)),
    [cases, selectedCaseIds]
  );

  // Fund the largest budget any selected case declares: an arm that runs out of
  // steps failed for a reason that is not the treatment.
  const maxSteps = useMemo(
    () => Math.max(10, ...selectedCases.map((item) => item.max_steps ?? 0)),
    [selectedCases]
  );

  // The union across the selected cases, exactly as the run form takes it — a
  // case without the chosen variant simply contributes no runs for it.
  const promptVariant = useMemo(() => {
    const names = new Set<string>();
    for (const item of selectedCases) {
      for (const variant of Object.keys(item.prompt_variants ?? {})) {
        names.add(variant);
      }
    }
    if (names.has("default")) {
      return "default";
    }
    return [...names].sort()[0] ?? "baseline";
  }, [selectedCases]);

  const stagedSelected = selectedSkills.filter((name) =>
    stagedSkills.some((skill) => skill.name === name)
  );

  const toggle = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const openPreset = (next: PresetKey) => {
    setPreset(next);
    setWarnings([]);
    setNotice("");
    setError(null);
    if (next === "skills" && stagedSkills.length === 0) {
      void loadSkills();
    }
  };

  /** Why a preset cannot be launched yet, or an empty string when it can. */
  const blocking = useMemo(() => {
    if (pinnedCommit === "") {
      return m.experimentsTab.blockingNoPin;
    }
    if (selectedCaseIds.length === 0) {
      return m.experimentsTab.blockingNoCases;
    }
    if (preset === "skills") {
      if (selectedSkills.length === 0) {
        return m.experimentsTab.blockingNoSkills;
      }
    }
    return "";
  }, [
    enforceInvoke,
    m,
    pinnedCommit,
    preset,
    selectedCaseIds,
    selectedSkills,
    stagedSelected
  ]);

  /** Create under the first free id: a name collision is not a failure. */
  const createUnderFreeId = async (
    base: string,
    build: (experimentId: string) => Record<string, unknown>
  ) => {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const candidate = attempt === 0 ? base : `${base}-${attempt + 1}`;
      try {
        return await api.createExperiment(build(candidate));
      } catch (createError) {
        if (!isDuplicateId(createError)) {
          throw createError;
        }
        lastError = createError;
      }
    }
    throw lastError instanceof Error ? lastError : new Error(m.errors.findFreeId);
  };

  const launchPreset = () =>
    run("launch", async () => {
      const name = target?.name ?? targetId;
      const shared = {
        target_repo_id: targetId,
        target_ref: pinnedCommit,
        harness_source: "native" as const
      };
      const isSkills = preset === "skills";
      const skillList = selectedSkills.join(", ");
      const conditions = isSkills
        ? [
            {
              label: "native",
              role: "control" as const,
              description: m.experimentsTab.conditionNative,
              overrides: { ...shared }
            },
            {
              label: "native + skills",
              role: "treatment" as const,
              description: enforceInvoke
                ? m.experimentsTab.conditionSkillsWithEnforce(skillList)
                : m.experimentsTab.conditionSkillsNoEnforce(skillList),
              overrides: {
                ...shared,
                // Only staged skills reach a `native` arm (through skills_extra);
                // the lab's governed skills are not in the target's governance.
                skills_extra: stagedSelected,
                ...(enforceInvoke ? { skills_invoke: [...stagedSelected] } : {})
              }
            }
          ]
        : [
            {
              label: "native",
              role: "control" as const,
              description: m.experimentsTab.conditionNativeGovernance,
              overrides: { ...shared }
            },
            {
              label: "injected",
              role: "treatment" as const,
              description: m.experimentsTab.conditionInjected,
              overrides: { ...shared, harness_source: "injected" as const }
            }
          ];

      const question = isSkills
        ? m.experimentsTab.questionSkills(skillList, name)
        : m.experimentsTab.questionHarness(name);
      const base = slugify(
        isSkills ? `skills-${targetId}` : `harness-source-${targetId}`
      );

      const created = await createUnderFreeId(base, (experimentId) => ({
        experiment_id: experimentId,
        name: isSkills
          ? m.experimentsTab.experimentNameSkills(name)
          : m.experimentsTab.experimentNameHarness(name),
        question,
        mode: "harness_evaluation",
        case_ids: [...selectedCaseIds],
        repetitions,
        conditions,
        notes: m.experimentsTab.runNotes(targetId, pinnedCommit.slice(0, 10))
      }));
      setWarnings(created.warnings ?? []);

      const result = await api.launchExperiment(created.experiment_id, {
        case_id: selectedCaseIds[0] ?? "",
        prompt_variant: promptVariant,
        harness: LAUNCH_HARNESS,
        planner_model: null,
        executor_model: executorModel.trim() || null,
        score_mode: "manual",
        max_steps: maxSteps,
        resume: false,
        dry_run: false
      });
      setNotice(m.experimentsTab.queuedNotice(result.count, created.experiment_id));
      setPreset("");
      setExperiments(await loadExperiments());
    });

  if (isLoading) {
    return (
      <section className="page">
        <Spinner label={m.loadingRepository} />
      </section>
    );
  }

  if (target === null) {
    return (
      <section className="page">
        <EmptyState
          action={
            <Link className="button-link" to="/evidence/repositories">
              {m.notFound.back}
            </Link>
          }
          description={m.notFound.description(targetId)}
          title={m.notFound.title}
        />
      </section>
    );
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>{target.name || target.target_id}</h1>
          <p className="lede">
            <code className="mono">{target.repository_url}</code>
          </p>
          <p className="muted small">
            {pinnedCommit ? (
              m.header.pinnedAt(
                <span className="badge neutral" title={pinnedCommit}>
                  {pinnedCommit.slice(0, 10)}
                </span>
              )
            ) : (
              <>
                <span className="badge warn">{m.header.unpinned}</span>{" "}
                {m.header.unpinnedNote}
              </>
            )}
          </p>
        </div>
        <div className="page-header-action">
          <Link className="button-link" to="/evidence/repositories">
            {m.header.allRepositories}
          </Link>
        </div>
      </header>

      <div className="tab-bar tab-bar--inline">
        {TAB_KEYS.map((key) => (
          <button
            className={`tab ${tab === key ? "tab--active" : ""}`}
            key={key}
            onClick={() => {
              setTab(key);
              setNotice("");
            }}
            type="button"
          >
            {m.tabs[key]}
          </button>
        ))}
      </div>

      {error !== null ? (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      ) : null}
      {notice ? <p className="callout callout--ok">{notice}</p> : null}

      {tab === "checkout" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.checkout.title}</h2>
            <span className={`badge ${checkout?.exists ? "ok" : "warn"}`}>
              {checkout?.exists ? m.checkout.cloned : m.checkout.notCloned}
            </span>
          </div>
          <p className="muted">{m.checkout.intro}</p>

          {checkout === null ? (
            <Spinner label={m.checkout.reading} />
          ) : (
            <>
              <div className="detail-grid">
                <div>
                  <span>{m.checkout.path}</span>
                  <code className="mono small">{checkout.host_path || "—"}</code>
                  {checkout.attached ? (
                    // The directory is named after the clone, not after this
                    // repository. Unsaid, the path on screen reads as a typo.
                    <span className="muted small">{m.checkout.attachedNote}</span>
                  ) : null}
                </div>
                <div>
                  <span>{m.checkout.head}</span>
                  <strong className="mono">
                    {checkout.head ? checkout.head.slice(0, 10) : "—"}
                  </strong>
                </div>
                <div>
                  <span>{m.checkout.branch}</span>
                  <strong>{checkout.branch || "—"}</strong>
                </div>
                <div>
                  <span>{m.checkout.pinnedCommit}</span>
                  <strong className="mono">
                    {checkout.pinned_commit
                      ? checkout.pinned_commit.slice(0, 10)
                      : m.checkout.notPinned}
                  </strong>
                </div>
                <div>
                  <span>{m.checkout.pinnedAtLabel}</span>
                  <strong>
                    {checkout.pinned_at ? formatRunDate(checkout.pinned_at) : "—"}
                  </strong>
                </div>
              </div>

              {checkout.error ? (
                <p className="callout callout--warning">{checkout.error}</p>
              ) : null}

              {checkout.dirty ? (
                <div className="callout callout--warning">
                  <div>
                    <strong>{m.checkout.dirtyTitle}</strong>
                    <p>
                      {m.checkout.dirtyBody(
                        checkout.dirty_files,
                        checkout.head ? checkout.head.slice(0, 10) : "HEAD"
                      )}
                    </p>
                  </div>
                </div>
              ) : null}

              {checkout.pin_is_stale ? (
                <div className="callout">
                  <div>
                    <strong>{m.checkout.staleTitle}</strong>
                    <p>{m.checkout.staleBody}</p>
                    <div className="button-row">
                      <button
                        className="primary"
                        disabled={busy !== ""}
                        onClick={() => void rePin()}
                        type="button"
                      >
                        {busy === "pin" ? m.checkout.pinning : m.checkout.rePin}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}

              {!checkout.exists || !checkout.is_repo ? (
                <div className="card">
                  <div className="panel-head">
                    <h3>{m.checkout.cloneYourself}</h3>
                    <button
                      className="secondary"
                      onClick={() => {
                        void navigator.clipboard.writeText(checkout.command);
                        setNotice(m.checkout.copiedNotice);
                      }}
                      type="button"
                    >
                      {m.checkout.copy}
                    </button>
                  </div>
                  <pre className="command-preview">{checkout.command}</pre>
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      {tab === "readiness" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>{m.readinessTab.title}</h2>
              {readiness !== null ? (
                <span className={`badge ${readiness.ready ? "ok" : "warn"}`}>
                  {readiness.ready ? m.readinessTab.ready : m.readinessTab.notReady}
                </span>
              ) : null}
            </div>
            {busy === "readiness" ? <Spinner label={m.readinessTab.reading} /> : null}
            {readiness !== null ? (
              <ReadinessReportView
                adopting={busy === "adopt"}
                onAdopt={(payload) => {
                  void run("adopt", async () => {
                    await api.updateTarget(targetId, payload);
                    setReadiness(await api.getTargetReadiness(targetId));
                    setNotice(m.readinessTab.adoptedNotice);
                  });
                }}
                report={readiness}
              />
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>{m.readinessTab.gateTitle}</h2>
              <button
                className="primary"
                disabled={busy !== "" || gate?.status === "running"}
                onClick={() => void runGateCheck()}
                type="button"
              >
                {gate?.status === "running" ? m.readinessTab.running : m.readinessTab.runGateCheck}
              </button>
            </div>
            <p className="muted">{m.readinessTab.gateIntro}</p>

            {gate === null ? (
              <EmptyState
                description={m.readinessTab.noGateDescription}
                title={m.readinessTab.noGateTitle}
              />
            ) : (
              <>
                <div className="detail-grid">
                  <div>
                    <span>{m.readinessTab.verdict}</span>
                    <strong>
                      {gate.status === "running"
                        ? m.readinessTab.verdictRunning
                        : gate.passed === true
                          ? m.readinessTab.verdictPassed
                          : gate.passed === false
                            ? m.readinessTab.verdictFailed(
                                gate.failed_phase || m.readinessTab.unnamedPhase
                              )
                            : m.readinessTab.verdictNone}
                    </strong>
                  </div>
                  <div>
                    <span>{m.readinessTab.commit}</span>
                    <strong className="mono">
                      {gate.commit ? gate.commit.slice(0, 10) : "—"}
                    </strong>
                  </div>
                  <div>
                    <span>{m.readinessTab.lastUpdated}</span>
                    <strong>{formatRunDate(gate.updated_at)}</strong>
                  </div>
                </div>
                {gate.error ? (
                  <p className="callout callout--danger">{gate.error}</p>
                ) : null}
                {gate.status === "running" ? (
                  <Spinner label={m.readinessTab.waitingForGate} />
                ) : null}
                <ul className="check-list">
                  {gate.checks_json.map((step) => (
                    <li
                      className={`check check--${step.passed ? "ok" : "missing"}`}
                      key={`${step.phase}-${step.command}`}
                    >
                      <div>
                        <strong>{step.phase}</strong>{" "}
                        <code className="mono small">{step.command}</code>
                        <p className="muted">
                          {m.readinessTab.exit(String(step.exit_code ?? "—"))} ·{" "}
                          {step.passed ? m.readinessTab.passed : m.readinessTab.failed}
                        </p>
                        {!step.passed && step.detail ? (
                          <pre className="command-preview">{step.detail}</pre>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </>
      ) : null}

      {tab === "cases" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.casesTab.title}</h2>
            {cases !== null ? (
              <span className="badge neutral">{m.casesTab.count(cases.length)}</span>
            ) : null}
          </div>
          <p className="muted">{m.casesTab.intro}</p>
          {busy === "cases" ? <Spinner label={m.casesTab.loading} /> : null}
          {cases !== null && cases.length === 0 ? (
            <EmptyState
              action={
                <Link className="button-link" to="/evaluate/audit">
                  {m.casesTab.deriveFromHistory}
                </Link>
              }
              description={m.casesTab.noneDescription}
              title={m.casesTab.noneTitle}
            />
          ) : null}
          {cases !== null && cases.length > 0 ? (
            <ul className="candidate-list">
              {cases.map((item) => (
                <li key={item.case_id}>
                  <button
                    className="skills-toggle"
                    onClick={() =>
                      setExpandedCase(expandedCase === item.case_id ? "" : item.case_id)
                    }
                    type="button"
                  >
                    {expandedCase === item.case_id ? "▾" : "▸"}{" "}
                    {item.name || item.case_id}
                    <span className="muted small">
                      {" "}
                      <code>{item.case_id}</code>{" "}
                      {m.casesTab.meta(item.type, String(item.max_steps ?? "—"))}
                    </span>
                  </button>
                  {expandedCase === item.case_id ? (
                    <div className="stack">
                      {(item.validation_commands ?? []).length === 0 ? (
                        <p className="muted small">{m.casesTab.noValidation}</p>
                      ) : (
                        <ul className="check-items">
                          {(item.validation_commands ?? []).map((command) => (
                            <li key={command}>
                              <code>{command}</code>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {tab === "experiments" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>{m.experimentsTab.launchTitle}</h2>
            </div>
            <p className="muted">{m.experimentsTab.launchIntro}</p>
            <div className="toolbar">
              <button
                className={preset === "skills" ? "primary" : "secondary"}
                onClick={() => openPreset(preset === "skills" ? "" : "skills")}
                type="button"
              >
                {m.experimentsTab.presetSkills}
              </button>
              <button
                className={preset === "harness" ? "primary" : "secondary"}
                onClick={() => openPreset(preset === "harness" ? "" : "harness")}
                type="button"
              >
                {m.experimentsTab.presetHarness}
              </button>
            </div>

            {preset !== "" ? (
              <div className="card">
                <h3>
                  {preset === "skills"
                    ? m.experimentsTab.presetSkills
                    : m.experimentsTab.presetHarness}
                </h3>
                <p className="muted small">
                  {preset === "skills"
                    ? m.experimentsTab.presetSkillsDescription
                    : m.experimentsTab.presetHarnessDescription}
                </p>

                <label className="field">
                  <span>{m.experimentsTab.casesSelected(selectedCaseIds.length)}</span>
                </label>
                {cases === null || cases.length === 0 ? (
                  <EmptyState
                    action={
                      <Link className="button-link" to="/evaluate/audit">
                        {m.experimentsTab.addFirstCase}
                      </Link>
                    }
                    description={m.experimentsTab.noCasesDescription}
                    title={m.experimentsTab.noCasesTitle}
                  />
                ) : (
                  <ul className="pick-list">
                    {cases.map((item) => {
                      const isSelected = selectedCaseIds.includes(item.case_id);
                      return (
                        <li key={item.case_id}>
                          <button
                            className={isSelected ? "primary" : "secondary"}
                            onClick={() =>
                              setSelectedCaseIds((current) =>
                                toggle(current, item.case_id)
                              )
                            }
                            type="button"
                          >
                            <strong>
                              {item.name || item.case_id}
                              <span className="badge pick-tag">{item.type}</span>
                            </strong>
                            <span className="muted">
                              <code>{item.case_id}</code> —{" "}
                              {m.experimentsTab.validationCommands(
                                (item.validation_commands ?? []).length
                              )}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}

                {preset === "skills" ? (
                  <>
                    <label className="field">
                      <span>{m.experimentsTab.skillsSelected(selectedSkills.length)}</span>
                    </label>
                    {busy === "skills" ? (
                      <Spinner label={m.experimentsTab.loadingSkills} />
                    ) : null}
                    {/* Staged skills only: a `native` arm sees the repository's own
                        governance, so a lab skill would be invoked without being
                        there. Staged skills are the ones added with skills_extra. */}
                    {busy !== "skills" && stagedSkills.length === 0 ? (
                      <p className="callout callout--warning small">
                        {m.experimentsTab.noStagedSkills}
                      </p>
                    ) : null}
                    <div className="checkbox-grid">
                      {stagedSkills.map((skill) => (
                        <label className="checkbox" key={`staged-${skill.name}`}>
                          <input
                            checked={selectedSkills.includes(skill.name)}
                            onChange={() =>
                              setSelectedSkills((current) => toggle(current, skill.name))
                            }
                            type="checkbox"
                          />
                          <span>
                            {skill.name}
                            <span className="badge pick-tag pick-tag--soft">
                              {m.experimentsTab.staged}
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                    <label className="inline-check">
                      <input
                        checked={enforceInvoke}
                        onChange={(event) => setEnforceInvoke(event.target.checked)}
                        type="checkbox"
                      />
                      <span>{m.experimentsTab.enforceSkillUse}</span>
                    </label>
                    <p className="muted small">{m.experimentsTab.enforceHint}</p>
                  </>
                ) : null}

                <div className="field-row">
                  <label className="field">
                    <span>{m.experimentsTab.repetitionsPerArm}</span>
                    <input
                      min={1}
                      onChange={(event) =>
                        setRepetitions(Math.max(1, Number(event.target.value)))
                      }
                      type="number"
                      value={repetitions}
                    />
                  </label>
                  <label className="field">
                    <span>{m.experimentsTab.executorModelLabel}</span>
                    <input
                      onChange={(event) => setExecutorModel(event.target.value)}
                      placeholder="gateway/<alias> · lmstudio/<id> · claude-sonnet-5"
                      value={executorModel}
                    />
                  </label>
                </div>

                {blocking ? <p className="callout callout--warning">{blocking}</p> : null}

                <div className="toolbar">
                  <button onClick={() => setPreset("")} type="button">
                    {m.experimentsTab.cancel}
                  </button>
                  <button
                    className="primary"
                    disabled={busy !== "" || blocking !== ""}
                    onClick={() => void launchPreset()}
                    type="button"
                  >
                    {busy === "launch"
                      ? m.experimentsTab.launching
                      : m.experimentsTab.createAndLaunch}
                  </button>
                </div>
              </div>
            ) : null}

            {warnings.length > 0 ? (
              <ul className="warning-list">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>{m.experimentsTab.onThisRepoTitle}</h2>
              {experiments !== null ? (
                <span className="badge neutral">{experiments.length}</span>
              ) : null}
            </div>
            {busy === "experiments" ? (
              <Spinner label={m.experimentsTab.readingExperiments} />
            ) : null}
            {experiments !== null && experiments.length === 0 ? (
              <EmptyState
                description={m.experimentsTab.noneRunDescription}
                title={m.experimentsTab.noneRunTitle}
              />
            ) : null}
            {experiments !== null && experiments.length > 0 ? (
              <ul className="pick-list">
                {experiments.map((item) => (
                  <li key={item.experiment_id}>
                    <Link
                      className="button-link"
                      to={`/evaluate/experiments?experiment=${item.experiment_id}`}
                    >
                      <strong>{item.name || item.experiment_id}</strong>
                      <span className="muted small">
                        {" "}
                        {m.experimentsTab.experimentMeta(
                          item.status,
                          item.conditions.length,
                          item.case_ids.length,
                          item.repetitions
                        )}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </>
      ) : null}

      {tab === "report" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.reportTab.title}</h2>
            <div className="toolbar">
              <button
                className="primary"
                disabled={busy !== ""}
                onClick={() => void generateReport()}
                type="button"
              >
                {busy === "report"
                  ? m.reportTab.assembling
                  : report
                    ? m.reportTab.regenerate
                    : m.reportTab.generate}
              </button>
              {report ? (
                <button onClick={downloadReport} type="button">
                  {m.reportTab.download}
                </button>
              ) : null}
            </div>
          </div>
          <p className="muted">{m.reportTab.intro}</p>
          {report ? (
            <pre className="candidate-prompt pre--scroll">{report}</pre>
          ) : (
            <EmptyState
              description={m.reportTab.noneDescription}
              title={m.reportTab.noneTitle}
            />
          )}
          <p className="muted small">{m.reportTab.findingsNote}</p>
        </div>
      ) : null}
    </section>
  );
}
