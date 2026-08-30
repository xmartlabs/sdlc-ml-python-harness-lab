import { useEffect, useState } from "react";

import { api } from "../api";
import type { ScoreRecord } from "../types";

const CATEGORY_LABEL: Record<string, string> = {
  outcome: "Outcome",
  efficiency: "Efficiency",
  trajectory: "Trajectory",
  behavioral: "Behaviour"
};

/**
 * The three kinds of claim, drawn differently on purpose.
 *
 * `test_pass_rate = 1.0` is a command's exit status. `skill_adherence = 0.81`
 * is a model's opinion. Once both are numbers in a table they look identical,
 * and the opinion borrows the authority of the fact.
 */
const PROVENANCE_HINT: Record<string, string> = {
  fact: "a command exited — reproducible without judgement",
  observation: "parsed from the run's artifacts — true of the record",
  judgement: "a model's opinion — defensible, not checkable"
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
        Object.entries(byCategory).map(([category, rows]) => (
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
                    <td className={`small provenance-${score.provenance}`}>
                      {score.provenance}
                      <div className="muted small">
                        {PROVENANCE_HINT[score.provenance]}
                      </div>
                    </td>
                    <td className="muted small">{score.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}
    </section>
  );
}
