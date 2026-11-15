import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { EffortPicker } from "../components/EffortPicker";
import { ErrorBanner } from "../components/ErrorBanner";
import { ModelPicker } from "../components/ModelPicker";
import { IconArrowRight, IconCompare, IconPlus, IconX } from "../components/icons";
import { HARNESS_NATIVE_MODELS, useNativeModels } from "../harnesses";
import { defineMessages, useMessages } from "../i18n";
import type { CaseRecord, ModelOption } from "../types";

/**
 * Compare models on one case.
 *
 * The question a new frontier model raises: how does it do on the case the
 * last one did? One case, two to four models — each with the harness that runs
 * it — and every other setting shared, so the models are the only difference.
 *
 * Underneath it is a `subject_validation` experiment (ADR-0040): no control
 * arm, one result per arm, never pooled. Every comparison on a case is filed
 * in the family `models-<case>`, so the experiments page lists them together
 * and a new model's comparison sits next to the last one.
 */

type Arm = { harness: string; model: string; effort: string };

const HARNESSES = ["claude", "opencode"] as const;
const MAX_ARMS = 4;
const MAX_SLUG = 60;
const FAMILY_PREFIX = "models-";

const MESSAGES = defineMessages({
    en: {
        eyebrow: "Compare models",
        title: "How do these models do on one case?",
        lede: "Pick one case and the models to run on it. Every model gets the same prompt, the same steps and the same repetitions, so the model is the only thing that differs. The result is a table per model: checks passed, phase by phase, with cost and time.",
        caseTitle: "1. Case",
        caseHint:
            "A hard case separates models best: the 3D game cases have 40 or more checks and are paced in phases, so even a partial build is measured.",
        noCases: "No cases yet.",
        createOne: "Create or import one",
        phases: (n: number) => `${n} phases`,
        checksDeclared: (n: number) => `${n} checks`,
        modelsTitle: "2. Models",
        modelsHint:
            "Each model runs with the harness that drives it: claude for Claude models, opencode for local and gateway models. The pair is what is compared.",
        harness: "Harness",
        model: "Model",
        pickModel: "Pick a model",
        addModel: "Add a model",
        remove: "Remove",
        settingsTitle: "3. Shared settings",
        variant: "Prompt variant",
        repetitions: "Repetitions per model",
        steps: "Steps per run",
        stepsHint: (n: number) => `The case declares ${n}.`,
        question: "Question",
        defaultQuestion: (caseId: string) => `Which model builds ${caseId} best?`,
        summary: (models: number, reps: number, runs: number) =>
            `${models} models x ${reps} repetition(s) = ${runs} runs.`,
        sequential:
            "Runs execute one at a time. A phased game case can take an hour or more per run, so watch the first one before waiting for all of them.",
        pace: "How the runs execute",
        oneAtATime: "One at a time (default)",
        oneAtATimeNote: "The lab's one lane. Uses your token quota at the usual pace.",
        twoAtATime: "Two at a time",
        twoAtATimeNote:
            "One model on each lane, so a fast cloud model finishes (and can be played) while a slower local one is still going.",
        twoCost: (cases: string) =>
            `Twice the token quota per hour, two containers at once (${cases}), and the CPU shared between them, so each run is a little slower. Two runs on the same local model take turns on its GPU and gain almost nothing.`,
        twoCostGame: "8 GB of memory for two game runs",
        twoCostPlain: "about twice the memory",
        laneDown: "The second lane is not running. Restart the stack (make app-up) to start it.",
        laneUnknown: "Could not ask the workers whether the second lane is up.",
        twoSummary: (runs: number) =>
            `${runs} runs, two at a time: about ${Math.ceil(runs / 2)} run(s) of wall time instead of ${runs}.`,
        family: (caseId: string) => (
            <>
                Filed with every earlier comparison on this case:{" "}
                <Link to={`/evaluate/experiments?family=${FAMILY_PREFIX}${caseId}`}>
                    {FAMILY_PREFIX}
                    {caseId}
                </Link>
                .
            </>
        ),
        pickCase: "Pick a case.",
        pickTwoModels: "Pick at least two models.",
        duplicate: "The same harness and model appear twice.",
        noVariant: "This case declares no prompt variant.",
        create: "Create without launching",
        launch: (runs: number) => `Create and launch ${runs} runs`,
        unableToLoad: "Unable to load cases",
        unableToCreate: "Unable to create the comparison",
        launchRefused: (id: string, reason: string) =>
            `Created '${id}', but the launch was refused: ${reason}. Launch it from its page.`,
    },
    es: {
        eyebrow: "Comparar modelos",
        title: "¿Cómo les va a estos modelos en un caso?",
        lede: "Elegí un caso y los modelos que lo van a correr. Todos reciben el mismo prompt, los mismos pasos y las mismas repeticiones, así que lo único que cambia es el modelo. El resultado es una tabla por modelo: chequeos aprobados, fase por fase, con costo y tiempo.",
        caseTitle: "1. Caso",
        caseHint:
            "Un caso difícil separa mejor a los modelos: los casos de juegos 3D tienen 40 chequeos o más y van por fases, así que hasta un juego a medio hacer se mide.",
        noCases: "Todavía no hay casos.",
        createOne: "Creá o importá uno",
        phases: (n: number) => `${n} fases`,
        checksDeclared: (n: number) => `${n} chequeos`,
        modelsTitle: "2. Modelos",
        modelsHint:
            "Cada modelo corre con el harness que lo maneja: claude para los modelos de Claude, opencode para los locales y los del gateway. Lo que se compara es el par.",
        harness: "Harness",
        model: "Modelo",
        pickModel: "Elegí un modelo",
        addModel: "Agregar un modelo",
        remove: "Quitar",
        settingsTitle: "3. Configuración común",
        variant: "Variante de prompt",
        repetitions: "Repeticiones por modelo",
        steps: "Pasos por corrida",
        stepsHint: (n: number) => `El caso declara ${n}.`,
        question: "Pregunta",
        defaultQuestion: (caseId: string) => `¿Qué modelo construye mejor ${caseId}?`,
        summary: (models: number, reps: number, runs: number) =>
            `${models} modelos x ${reps} repetición(es) = ${runs} corridas.`,
        sequential:
            "Las corridas se ejecutan de a una. Un caso de juego por fases puede tardar una hora o más por corrida: mirá la primera antes de esperar a todas.",
        pace: "Cómo se ejecutan las corridas",
        oneAtATime: "De a una (por defecto)",
        oneAtATimeNote: "El único carril del lab. Consume tu cuota de tokens al ritmo de siempre.",
        twoAtATime: "De a dos",
        twoAtATimeNote:
            "Un modelo en cada carril: un modelo en la nube, que es rápido, termina (y lo podés jugar) mientras uno local más lento sigue corriendo.",
        twoCost: (cases: string) =>
            `El doble de cuota de tokens por hora, dos contenedores a la vez (${cases}) y la CPU compartida entre los dos, así que cada corrida va un poco más lenta. Dos corridas sobre el mismo modelo local se turnan en su GPU y casi no ganan nada.`,
        twoCostGame: "8 GB de memoria para dos juegos",
        twoCostPlain: "cerca del doble de memoria",
        laneDown:
            "El segundo carril no está corriendo. Reiniciá el stack (make app-up) para levantarlo.",
        laneUnknown: "No se pudo preguntar a los workers si el segundo carril está levantado.",
        twoSummary: (runs: number) =>
            `${runs} corridas, de a dos: cerca de ${Math.ceil(runs / 2)} corrida(s) de tiempo real en lugar de ${runs}.`,
        family: (caseId: string) => (
            <>
                Queda junto a las comparaciones anteriores de este caso:{" "}
                <Link to={`/evaluate/experiments?family=${FAMILY_PREFIX}${caseId}`}>
                    {FAMILY_PREFIX}
                    {caseId}
                </Link>
                .
            </>
        ),
        pickCase: "Elegí un caso.",
        pickTwoModels: "Elegí al menos dos modelos.",
        duplicate: "El mismo harness y modelo aparecen dos veces.",
        noVariant: "Este caso no declara ninguna variante de prompt.",
        create: "Crear sin lanzar",
        launch: (runs: number) => `Crear y lanzar ${runs} corridas`,
        unableToLoad: "No se pudieron cargar los casos",
        unableToCreate: "No se pudo crear la comparación",
        launchRefused: (id: string, reason: string) =>
            `Se creó '${id}', pero el lanzamiento fue rechazado: ${reason}. Lanzalo desde su página.`,
    },
});

function slugify(value: string): string {
    return value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}

/** A short, readable arm label: `opus-5-5`, `bonsai-27b`. */
function armLabel(arm: Arm): string {
    const tail = arm.model.split("/").pop() ?? arm.model;
    const base = slugify(tail.replace(/^claude-/, "")) || "model";
    return arm.effort ? `${base}-${arm.effort}` : base;
}

// The graded behaviours of a lab grader (`grade.py check <name>`), one per
// command; zero for a case graded another way, and then nothing is shown.
function declaredChecks(row: CaseRecord): number {
    return (row.validation_commands ?? []).filter((command) => /grade\.py check \w/.test(command))
        .length;
}

export function CompareModels() {
    const m = useMessages(MESSAGES);
    const navigate = useNavigate();
    const native = useNativeModels();
    const [cases, setCases] = useState<CaseRecord[]>([]);
    const [caseId, setCaseId] = useState("");
    const [arms, setArms] = useState<Arm[]>([
        { harness: "claude", model: "claude-opus-5-5", effort: "" },
        { harness: "claude", model: "claude-sonnet-5-5", effort: "" },
    ]);
    const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
    const [directErrors, setDirectErrors] = useState<Record<string, string>>({});
    const [reloading, setReloading] = useState(false);
    const [variant, setVariant] = useState("");
    const [repetitions, setRepetitions] = useState(1);
    const [maxSteps, setMaxSteps] = useState(5);
    // 1 is the lab's one lane; 2 alternates the runs over a second worker.
    const [pace, setPace] = useState<1 | 2>(1);
    // Whether a worker serves the second lane: null until asked, or when no
    // worker answered.
    const [laneUp, setLaneUp] = useState<boolean | null>(null);
    const [question, setQuestion] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        api.listCases()
            .then((rows) => setCases(rows.filter((row) => row.status !== "archived")))
            .catch((loadError: unknown) =>
                setError(loadError instanceof Error ? loadError.message : m.unableToLoad),
            );
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const loadModels = (refresh = false) => {
        setReloading(refresh);
        api.listModels(refresh)
            .then((catalog) => {
                setModelOptions(catalog.models);
                setDirectErrors(catalog.direct_errors ?? {});
            })
            .catch(() => setModelOptions([]))
            .finally(() => setReloading(false));
    };
    useEffect(() => loadModels(), []);
    useEffect(() => {
        api.getRunLanes()
            .then((lanes) => setLaneUp(lanes.parallel))
            .catch(() => setLaneUp(null));
    }, []);

    const selected = useMemo(
        () => cases.find((row) => row.case_id === caseId) ?? null,
        [cases, caseId],
    );
    const variants = Object.keys(selected?.prompt_variants ?? {});

    // A new case brings its own defaults: its steps (one per phase) and a
    // variant it declares, the plain one first.
    useEffect(() => {
        if (!selected) return;
        setMaxSteps(selected.max_steps ?? 5);
        const names = Object.keys(selected.prompt_variants ?? {});
        setVariant(names.includes("default") ? "default" : (names[0] ?? ""));
        setQuestion(m.defaultQuestion(selected.case_id));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selected?.case_id]);

    const chosen = arms.filter((arm) => arm.model.trim());
    const labels = chosen.map(armLabel);
    const duplicate =
        new Set(chosen.map((arm) => `${arm.harness}|${arm.model}|${arm.effort}`)).size !==
        chosen.length;
    const runs = chosen.length * repetitions;
    // A case that brings its own machine is a game: each run takes 4 GB.
    const isGameCase = Boolean(selected?.metadata_json?.runner_image);

    const problems: string[] = [];
    if (!selected) problems.push(m.pickCase);
    if (chosen.length < 2) problems.push(m.pickTwoModels);
    if (duplicate) problems.push(m.duplicate);
    if (pace === 2 && laneUp !== true)
        problems.push(laneUp === false ? m.laneDown : m.laneUnknown);
    if (selected && variants.length === 0) problems.push(m.noVariant);
    const blocking = problems.length > 0;

    const updateArm = (index: number, patch: Partial<Arm>) =>
        setArms((current) => current.map((arm, i) => (i === index ? { ...arm, ...patch } : arm)));

    const create = async (launch: boolean) => {
        if (blocking || !selected) return;
        setSaving(true);
        setError(null);
        // Unique labels even when two arms share a model tail.
        const unique = labels.map((label, index) =>
            labels.indexOf(label) === index ? label : `${label}-${index + 1}`,
        );
        const base = `${selected.case_id}-models-${unique.join("-vs-")}`.slice(0, MAX_SLUG);
        const taken = new Set(
            await api
                .listExperiments()
                .then((rows) => rows.map((row) => row.experiment_id))
                .catch(() => [] as string[]),
        );
        let experimentId = slugify(base);
        for (let counter = 2; taken.has(experimentId) && counter < 100; counter += 1) {
            experimentId = `${slugify(base).slice(0, MAX_SLUG - 4)}-${counter}`;
        }
        const conditions = chosen.map((arm, index) => ({
            label: unique[index],
            // No control: every model is a peer (ADR-0040).
            role: "treatment",
            overrides: {
                harness: arm.harness,
                executor_model: arm.model,
                ...(arm.effort ? { executor_effort: arm.effort } : {}),
            },
        }));
        let createdId: string;
        try {
            const created = await api.createExperiment({
                experiment_id: experimentId,
                name: question.slice(0, 80),
                question,
                mode: "subject_validation",
                subject: `model comparison on ${selected.case_id}`,
                case_ids: [selected.case_id],
                prompt_variants: [variant],
                repetitions,
                family_id: `${FAMILY_PREFIX}${selected.case_id}`,
                conditions,
            });
            createdId = created.experiment_id;
        } catch (saveError) {
            setError(saveError instanceof Error ? saveError.message : m.unableToCreate);
            setSaving(false);
            return;
        }
        const page = `/evaluate/experiments?experiment=${createdId}`;
        if (launch) {
            try {
                // The shared settings; each arm's harness and model override them.
                await api.launchExperiment(
                    createdId,
                    {
                        case_id: selected.case_id,
                        prompt_variant: variant,
                        harness: chosen[0].harness,
                        planner_model: null,
                        executor_model: chosen[0].model,
                        score_mode: "manual",
                        max_steps: maxSteps,
                        resume: false,
                        dry_run: false,
                    },
                    pace,
                );
            } catch (launchError) {
                setSaving(false);
                navigate(page, {
                    state: {
                        launchError: m.launchRefused(
                            createdId,
                            launchError instanceof Error
                                ? launchError.message
                                : String(launchError),
                        ),
                    },
                });
                return;
            }
        }
        setSaving(false);
        navigate(page);
    };

    return (
        <section className="page">
            <header className="page-header">
                <div>
                    <span className="eyebrow">
                        <IconCompare size={14} /> {m.eyebrow}
                    </span>
                    <h1>{m.title}</h1>
                    <p className="lede">{m.lede}</p>
                </div>
            </header>

            {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

            <section className="panel">
                <h2>{m.caseTitle}</h2>
                <p className="muted small">{m.caseHint}</p>
                {cases.length === 0 ? (
                    <p className="muted small">
                        {m.noCases} <Link to="/evidence/cases">{m.createOne}</Link>.
                    </p>
                ) : (
                    <div className="checkbox-stack">
                        {cases.map((row) => {
                            const checks = declaredChecks(row);
                            const phases = (row.step_prompts?.length ?? 0) + 1;
                            return (
                                <label className="checkbox" key={row.case_id}>
                                    <input
                                        checked={caseId === row.case_id}
                                        name="compare-case"
                                        onChange={() => setCaseId(row.case_id)}
                                        type="radio"
                                    />
                                    <span>
                                        <strong>{row.name}</strong>{" "}
                                        <span className="muted small mono">{row.case_id}</span>
                                        {phases > 1 || checks > 0 ? (
                                            <span className="muted small">
                                                {" · "}
                                                {[
                                                    phases > 1 ? m.phases(phases) : "",
                                                    checks > 0 ? m.checksDeclared(checks) : "",
                                                ]
                                                    .filter(Boolean)
                                                    .join(" · ")}
                                            </span>
                                        ) : null}
                                    </span>
                                </label>
                            );
                        })}
                    </div>
                )}
            </section>

            <section className="panel">
                <h2>{m.modelsTitle}</h2>
                <p className="muted small">{m.modelsHint}</p>
                <div className="compare-arms">
                    {arms.map((arm, index) => (
                        <div className="compare-arm" key={index}>
                            <label className="field">
                                <span>{m.harness}</span>
                                <select
                                    onChange={(event) =>
                                        updateArm(index, {
                                            harness: event.target.value,
                                            model: "",
                                            effort: "",
                                        })
                                    }
                                    value={arm.harness}
                                >
                                    {HARNESSES.map((name) => (
                                        <option key={name} value={name}>
                                            {name}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            {arm.harness === "claude" ? (
                                <label className="field">
                                    <span>{m.model}</span>
                                    <select
                                        onChange={(event) =>
                                            updateArm(index, { model: event.target.value })
                                        }
                                        value={arm.model}
                                    >
                                        <option value="">{m.pickModel}</option>
                                        {(native.claude ?? HARNESS_NATIVE_MODELS.claude).map(
                                            (model) => (
                                                <option key={model.id} value={model.id}>
                                                    {model.label}
                                                </option>
                                            ),
                                        )}
                                    </select>
                                </label>
                            ) : (
                                <ModelPicker
                                    directErrors={directErrors}
                                    label={m.model}
                                    onChange={(model) => updateArm(index, { model })}
                                    onReload={() => loadModels(true)}
                                    options={modelOptions}
                                    reloading={reloading}
                                    value={arm.model}
                                />
                            )}
                            {arm.harness === "claude" && arm.model ? (
                                <EffortPicker
                                    harness={arm.harness}
                                    model={arm.model}
                                    onChange={(effort) => updateArm(index, { effort })}
                                    value={arm.effort}
                                />
                            ) : null}
                            <button
                                aria-label={m.remove}
                                className="ghost"
                                disabled={arms.length <= 2}
                                onClick={() =>
                                    setArms((current) => current.filter((_, i) => i !== index))
                                }
                                title={m.remove}
                                type="button"
                            >
                                <IconX size={15} />
                            </button>
                        </div>
                    ))}
                </div>
                {arms.length < MAX_ARMS ? (
                    <button
                        className="secondary"
                        onClick={() =>
                            setArms((current) => [
                                ...current,
                                { harness: "claude", model: "", effort: "" },
                            ])
                        }
                        type="button"
                    >
                        <IconPlus size={15} /> {m.addModel}
                    </button>
                ) : null}
            </section>

            <section className="panel">
                <h2>{m.settingsTitle}</h2>
                <div className="form-grid">
                    <label className="full">
                        {m.question}
                        <input
                            onChange={(event) => setQuestion(event.target.value)}
                            value={question}
                        />
                    </label>
                    <label>
                        {m.variant}
                        <select
                            disabled={variants.length === 0}
                            onChange={(event) => setVariant(event.target.value)}
                            value={variant}
                        >
                            {variants.map((name) => (
                                <option key={name} value={name}>
                                    {name}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label>
                        {m.repetitions}
                        <select
                            onChange={(event) => setRepetitions(Number(event.target.value))}
                            value={repetitions}
                        >
                            {[1, 2, 3, 5].map((value) => (
                                <option key={value} value={value}>
                                    {value}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label>
                        {m.steps}
                        <input
                            min={1}
                            max={40}
                            onChange={(event) =>
                                setMaxSteps(Math.max(1, Number(event.target.value) || 1))
                            }
                            type="number"
                            value={maxSteps}
                        />
                        {selected?.max_steps ? (
                            <small className="muted">{m.stepsHint(selected.max_steps)}</small>
                        ) : null}
                    </label>
                </div>
                <fieldset className="compare-pace">
                    <legend>{m.pace}</legend>
                    <label className="checkbox">
                        <input
                            checked={pace === 1}
                            name="compare-pace"
                            onChange={() => setPace(1)}
                            type="radio"
                        />
                        <span>
                            <strong>{m.oneAtATime}</strong>
                            <span className="muted small"> — {m.oneAtATimeNote}</span>
                        </span>
                    </label>
                    <label className="checkbox">
                        <input
                            checked={pace === 2}
                            disabled={laneUp !== true}
                            name="compare-pace"
                            onChange={() => setPace(2)}
                            type="radio"
                        />
                        <span>
                            <strong>{m.twoAtATime}</strong>
                            <span className="muted small"> — {m.twoAtATimeNote}</span>
                        </span>
                    </label>
                    {pace === 2 ? (
                        <p className="warn-text small">
                            {m.twoCost(isGameCase ? m.twoCostGame : m.twoCostPlain)}
                        </p>
                    ) : null}
                    {laneUp === false ? (
                        <p className="muted small">{m.laneDown}</p>
                    ) : laneUp === null ? (
                        <p className="muted small">{m.laneUnknown}</p>
                    ) : null}
                </fieldset>
                <p className="muted small">
                    {m.summary(chosen.length, repetitions, runs)}{" "}
                    {pace === 2 ? m.twoSummary(runs) : m.sequential}
                </p>
                {selected ? <p className="muted small">{m.family(selected.case_id)}</p> : null}
                {problems.length > 0 ? (
                    <ul className="muted small">
                        {problems.map((problem) => (
                            <li key={problem}>{problem}</li>
                        ))}
                    </ul>
                ) : null}
                <div className="panel-actions">
                    <button
                        className="secondary"
                        disabled={blocking || saving}
                        onClick={() => create(false)}
                        type="button"
                    >
                        {m.create}
                    </button>
                    <button
                        className="primary"
                        disabled={blocking || saving}
                        onClick={() => create(true)}
                        type="button"
                    >
                        {m.launch(runs)}
                        <IconArrowRight size={15} />
                    </button>
                </div>
            </section>
        </section>
    );
}
