import { useEffect, useState } from "react";

import { api } from "../api";
import { ProvenanceBadge, ProvenanceLegend } from "./ProvenanceBadge";
import type { ScoreRecord } from "../types";

const CATEGORY_LABEL: Record<string, string> = {
  outcome: "Outcome",
  efficiency: "Efficiency",
  trajectory: "Trajectory",
  behavioral: "Behaviour"
};

function displayValue(score: ScoreRecord): string {
  if (score.value_bool !== null) {
    return score.value_bool ? "pass" : "fail";
  }
  if (score.value_num !== null) {
    return Number(score.value_num.toPrecision(4)).toString();
  }
  return score.value_text ?? "—";
}

export function ScoresPanel({ runId }: { runId: string }) {
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
        evaluateError instanceof Error ? evaluateError.message : "Unable to score run"
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
        <h3>Scores</h3>
        <button disabled={isBusy} onClick={() => void handleEvaluate()} type="button">
          {isBusy ? "Scoring…" : scores.length ? "Re-score" : "Score this run"}
        </button>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {scores.length === 0 ? (
        <p className="muted">
          Not scored yet. Scoring reads the run's validation, diff and audit and
          records them against the versioned registry.
        </p>
      ) : (
        <>
        <ProvenanceLegend />
        {Object.entries(byCategory).map(([category, rows]) => (
          <div key={category}>
            <h4>{CATEGORY_LABEL[category] ?? category}</h4>
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
