import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { ModelComparison, ModelComparisonRun } from "../types";

/**
 * Models compared on one case: checks passed per run, phase by phase, with
 * cost and time. A frontier game case is rarely passed outright, so a pass
 * rate would read 0% for every model; the checks are what separate them.
 * Shown on an experiment of a `models-<case>` family (the Compare models page).
 */

const MESSAGES = defineMessages({
    en: {
        title: "Models on this case",
        intro: "Each run's checks after every phase, then its final grade. Every model got the same prompt, steps and repetitions; the harness is the one that drives that model.",
        model: "Model",
        run: "Run",
        phases: "Checks after each phase",
        final: "Final",
        cost: "Cost",
        time: "Time",
        mean: (value: string, best: string) => `mean ${value} · best ${best}`,
        notRun: "No run yet.",
        unknownCost: "not reported",
        previous: "Earlier comparisons on this case",
    },
    es: {
        title: "Modelos en este caso",
        intro: "Los chequeos de cada corrida después de cada fase, y después su nota final. Todos los modelos recibieron el mismo prompt, pasos y repeticiones; el harness es el que maneja a ese modelo.",
        model: "Modelo",
        run: "Corrida",
        phases: "Chequeos después de cada fase",
        final: "Final",
        cost: "Costo",
        time: "Tiempo",
        mean: (value: string, best: string) => `promedio ${value} · mejor ${best}`,
        notRun: "Todavía sin corridas.",
        unknownCost: "no reportado",
        previous: "Comparaciones anteriores en este caso",
    },
});

function percent(value: number | null): string {
    return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function minutes(value: number | null): string {
    if (value === null) return "—";
    if (value < 60) return `${Math.round(value)} min`;
    return `${Math.floor(value / 60)} h ${Math.round(value % 60)} min`;
}

function Phases({ run }: { run: ModelComparisonRun }) {
    if (run.phases.length === 0) return <span className="muted">—</span>;
    return (
        <span className="model-phases">
            {run.phases.map((phase) => {
                const ratio =
                    phase.passed !== null && phase.total ? phase.passed / phase.total : null;
                return (
                    <span
                        className="model-phase"
                        key={phase.step}
                        title={
                            phase.passed !== null
                                ? `${phase.passed}/${phase.total}`
                                : `step ${phase.step}`
                        }
                    >
                        <span className="run-phase-bar" aria-hidden="true">
                            <span style={{ width: `${Math.round((ratio ?? 0) * 100)}%` }} />
                        </span>
                        <span className="small mono">
                            {phase.passed !== null ? `${phase.passed}/${phase.total}` : "—"}
                        </span>
                    </span>
                );
            })}
        </span>
    );
}

export function ModelComparisonPanel({ experimentId }: { experimentId: string }) {
    const m = useMessages(MESSAGES);
    const [data, setData] = useState<ModelComparison | null>(null);

    useEffect(() => {
        let active = true;
        const load = () =>
            api
                .getModelComparison(experimentId)
                .then((result) => active && setData(result))
                .catch(() => undefined);
        load();
        // Runs of a game case take an hour each: a slow refresh is enough.
        const timer = window.setInterval(load, 30000);
        return () => {
            active = false;
            window.clearInterval(timer);
        };
    }, [experimentId]);

    if (!data) return null;

    return (
        <section className="card model-comparison">
            <h3>{m.title}</h3>
            <p className="muted small">{m.intro}</p>
            <table className="data-table">
                <thead>
                    <tr>
                        <th>{m.model}</th>
                        <th>{m.run}</th>
                        <th>{m.phases}</th>
                        <th>{m.final}</th>
                        <th>{m.cost}</th>
                        <th>{m.time}</th>
                    </tr>
                </thead>
                <tbody>
                    {data.arms.map((arm) => {
                        const head = (
                            <td rowSpan={Math.max(1, arm.runs.length)}>
                                <strong>{arm.executor_model || arm.label}</strong>
                                <div className="muted small mono">
                                    {arm.harness}
                                    {arm.executor_effort ? ` · ${arm.executor_effort}` : ""}
                                </div>
                                {arm.mean_pass_rate !== null ? (
                                    <div className="small">
                                        {m.mean(
                                            percent(arm.mean_pass_rate),
                                            percent(arm.best_pass_rate),
                                        )}
                                    </div>
                                ) : null}
                            </td>
                        );
                        if (arm.runs.length === 0) {
                            return (
                                <tr key={arm.label}>
                                    {head}
                                    <td className="muted" colSpan={5}>
                                        {m.notRun}
                                    </td>
                                </tr>
                            );
                        }
                        return arm.runs.map((run, index) => (
                            <tr key={run.run_id}>
                                {index === 0 ? head : null}
                                <td>
                                    <Link
                                        className="mono small"
                                        to={`/advanced/runs/${run.run_id}`}
                                    >
                                        {run.run_id}
                                    </Link>
                                    <div className="muted small">{run.state}</div>
                                </td>
                                <td>
                                    <Phases run={run} />
                                </td>
                                <td>
                                    {run.passed !== null ? (
                                        <strong>
                                            {run.passed}/{run.total}
                                        </strong>
                                    ) : (
                                        "—"
                                    )}
                                    <div className="muted small">{percent(run.pass_rate)}</div>
                                </td>
                                <td>
                                    {run.cost_usd !== null ? (
                                        `US$${run.cost_usd.toFixed(2)}`
                                    ) : (
                                        <span className="muted small">{m.unknownCost}</span>
                                    )}
                                </td>
                                <td>{minutes(run.minutes)}</td>
                            </tr>
                        ));
                    })}
                </tbody>
            </table>
            {data.family_id ? (
                <p className="muted small">
                    <Link to={`/evaluate/experiments?family=${data.family_id}`}>{m.previous}</Link>
                </p>
            ) : null}
        </section>
    );
}
