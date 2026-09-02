/**
 * Which kind of claim a number is (§9).
 *
 * `test_pass_rate = 1.0` is a command's exit status. `skill_file_read = true`
 * is a fact about the record of the run. `skill_usefulness = 0.81` is a model's
 * opinion. Once all three are numbers in the same table they look identical,
 * and the opinion quietly borrows the authority of the measurement — which is
 * the single easiest way for a lab like this to mislead the person reading it.
 */
export const PROVENANCE_HINT: Record<string, string> = {
  fact: "a command exited — reproducible without judgement",
  observation: "parsed from the run's artifacts — true of the record",
  judgement: "a model's opinion — defensible, not checkable"
};

const LABEL: Record<string, string> = {
  fact: "Fact",
  observation: "Observation",
  judgement: "Judgement"
};

export function ProvenanceBadge({ provenance }: { provenance: string }) {
  return (
    <span
      className={`provenance-badge provenance-${provenance}`}
      title={PROVENANCE_HINT[provenance] ?? provenance}
    >
      {LABEL[provenance] ?? provenance}
    </span>
  );
}

/** The key, shown once wherever the three kinds appear side by side. */
export function ProvenanceLegend() {
  return (
    <p className="provenance-legend">
      {Object.keys(LABEL).map((kind) => (
        <span key={kind}>
          <ProvenanceBadge provenance={kind} /> {PROVENANCE_HINT[kind]}
        </span>
      ))}
    </p>
  );
}
