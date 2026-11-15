import { useEffect } from "react";

import { effortLevelsFor, useNativeModelIndex } from "../harnesses";
import { defineMessages, useMessages } from "../i18n";

type EffortPickerProps = {
  harness: string;
  model: string;
  value: string;
  onChange: (effort: string) => void;
};

const MESSAGES = defineMessages({
  en: {
    label: "Reasoning effort",
    cliDefault: "Default (the CLI decides)",
    levels: {
      low: "Low — fastest, fewest tokens",
      medium: "Medium",
      high: "High",
      xhigh: "Extra high",
      max: "Max — most thorough, most tokens"
    } as Record<string, string>,
    help: "Passed to Claude Code as --effort. Same model at another effort is another arm: it changes the condition hash, so compare it like any other factor."
  },
  es: {
    label: "Esfuerzo de razonamiento",
    cliDefault: "Por defecto (decide el CLI)",
    levels: {
      low: "Bajo — más rápido, menos tokens",
      medium: "Medio",
      high: "Alto",
      xhigh: "Extra alto",
      max: "Máximo — más exhaustivo, más tokens"
    } as Record<string, string>,
    help: "Se pasa a Claude Code como --effort. El mismo modelo con otro esfuerzo es otro brazo: cambia el condition hash, así que compáralo como cualquier otro factor."
  }
});

/**
 * Reasoning effort for a claude run (ADR-0081).
 *
 * Renders nothing unless the harness is claude and the executor model accepts
 * effort, so every other form state looks exactly as it did before. When the
 * model changes to one that does not accept the chosen level, the choice falls
 * back to the CLI default instead of carrying a value the preflight would
 * refuse.
 */
export function EffortPicker({ harness, model, value, onChange }: EffortPickerProps) {
  const m = useMessages(MESSAGES);
  const index = useNativeModelIndex();
  const levels = effortLevelsFor(harness, model, index);
  // A string key, so the effect runs when the accepted set changes and not on
  // every render (the array is rebuilt each time).
  const accepted = levels.join(",");

  useEffect(() => {
    if (value && !accepted.split(",").includes(value)) {
      onChange("");
    }
  }, [accepted, value, onChange]);

  if (levels.length === 0) {
    return null;
  }
  return (
    <label>
      <span>{m.label}</span>
      <select onChange={(event) => onChange(event.target.value)} value={value}>
        <option value="">{m.cliDefault}</option>
        {levels.map((level) => (
          <option key={level} value={level}>
            {m.levels[level] ?? level}
          </option>
        ))}
      </select>
      <small className="muted">{m.help}</small>
    </label>
  );
}
