import { useEffect, useState } from "react";

import { api } from "../api";
import { defineMessages, pickMessages, useMessages } from "../i18n";
import { ProvenanceBadge, ProvenanceLegend } from "./ProvenanceBadge";
import type { ScoreRecord } from "../types";

const MESSAGES = defineMessages({
  en: {
    categoryLabel: {
      outcome: "Outcome",
      efficiency: "Efficiency",
      trajectory: "Trajectory",
      behavioral: "Behaviour"
    },
    title: "Scores",
    scoring: "Scoring…",
    rescore: "Re-score",
    scoreThisRun: "Score this run",
    unableToScore: "Unable to score run",
    notScoredYet:
      "Not scored yet. Scoring reads the run's validation, diff and audit and " +
      "records them against the versioned registry.",
    pass: "pass",
    fail: "fail"
  },
  es: {
    categoryLabel: {
      outcome: "Resultado",
      efficiency: "Eficiencia",
      trajectory: "Trayectoria",
      behavioral: "Comportamiento"
    },
    title: "Puntajes",
    scoring: "Puntuando…",
    rescore: "Re-puntuar",
    scoreThisRun: "Puntuar esta corrida",
    unableToScore: "No se pudo puntuar la corrida",
    notScoredYet:
      "Todavía no tiene puntaje. Al puntuar se leen la validación, el diff y la " +
      "auditoría de la corrida y se registran contra el registro versionado.",
    pass: "pasó",
    fail: "falló"
  }
});

function displayValue(score: ScoreRecord): string {
  const m = pickMessages(MESSAGES);
  if (score.value_bool !== null) {
    return score.value_bool ? m.pass : m.fail;
  }
  if (score.value_num !== null) {
    return Number(score.value_num.toPrecision(4)).toString();
  }
  return score.value_text ?? "—";
}

export function ScoresPanel({ runId }: { runId: string }) {
  const m = useMessages(MESSAGES);
  const [scores, setScores] = useState<ScoreRecord[]>([]);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listRunScores(runId)
      .then(setScores)
      .catch(() => setScores([]));
  }, [runId]);

  const handleEvaluate = async () => {
    setIsBusy(true);
    setError(null);
    try {
      setScores(await api.evaluateRunScores(runId));
    } catch (evaluateError) {
      setError(
        evaluateError instanceof Error ? evaluateError.message : m.unableToScore
      );
    } finally {
      setIsBusy(false);
    }
  };

  const byCategory = scores.reduce<Record<string, ScoreRecord[]>>((acc, score) => {
    (acc[score.category] ??= []).push(score);
    return acc;
  }, {});

  return (
    <section className="panel">
      <div className="row space-between">
        <h3>{m.title}</h3>
        <button disabled={isBusy} onClick={() => void handleEvaluate()} type="button">
          {isBusy ? m.scoring : scores.length ? m.rescore : m.scoreThisRun}
        </button>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {scores.length === 0 ? (
        <p className="muted">{m.notScoredYet}</p>
      ) : (
        <>
        <ProvenanceLegend />
        {Object.entries(byCategory).map(([category, rows]) => (
          <div key={category}>
            <h4>{m.categoryLabel[category as keyof typeof m.categoryLabel] ?? category}</h4>
            <table className="data-table">
              <tbody>
                {rows.map((score) => (
                  <tr key={`${score.name}@${score.version}`}>
                    <td className="mono small">
                      {score.name}
                      <span className="muted"> @{score.version}</span>
                    </td>
                    <td className="mono">{displayValue(score)}</td>
                    <td className="small">
                      <ProvenanceBadge provenance={score.provenance} />
                    </td>
                    <td className="muted small">{score.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        </>
      )}
    </section>
  );
}
