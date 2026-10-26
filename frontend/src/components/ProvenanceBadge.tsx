import { defineMessages, pickMessages, useMessages } from "../i18n";

/**
 * Which kind of claim a number is (§9).
 *
 * `test_pass_rate = 1.0` is a command's exit status. `skill_file_read = true`
 * is a fact about the record of the run. `skill_usefulness = 0.81` is a model's
 * opinion. Once all three are numbers in the same table they look identical,
 * and the opinion quietly borrows the authority of the measurement — which is
 * the single easiest way for a lab like this to mislead the person reading it.
 */
const MESSAGES = defineMessages<{ hint: Record<string, string>; label: Record<string, string> }>({
  en: {
    hint: {
      fact: "a command exited — reproducible without judgement",
      observation: "parsed from the run's artifacts — true of the record",
      judgement: "a model's opinion — defensible, not checkable"
    },
    label: { fact: "Fact", observation: "Observation", judgement: "Judgement" }
  },
  es: {
    hint: {
      fact: "un comando terminó: reproducible sin juicio",
      observation: "leído de los artefactos de la corrida: cierto del registro",
      judgement: "la opinión de un modelo: defendible, no verificable"
    },
    label: { fact: "Hecho", observation: "Observación", judgement: "Juicio" }
  }
});

/** The hint for one kind, in the current language. */
export function provenanceHint(provenance: string): string {
  return pickMessages(MESSAGES).hint[provenance] ?? provenance;
}

const KINDS = ["fact", "observation", "judgement"];

export function ProvenanceBadge({ provenance }: { provenance: string }) {
  const m = useMessages(MESSAGES);
  return (
    <span
      className={`provenance-badge provenance-${provenance}`}
      title={m.hint[provenance] ?? provenance}
    >
      {m.label[provenance] ?? provenance}
    </span>
  );
}

/** The key, shown once wherever the three kinds appear side by side. */
export function ProvenanceLegend() {
  const m = useMessages(MESSAGES);
  return (
    <p className="provenance-legend">
      {KINDS.map((kind) => (
        <span key={kind}>
          <ProvenanceBadge provenance={kind} /> {m.hint[kind]}
        </span>
      ))}
    </p>
  );
}
