import { defineMessages, useMessages } from "../i18n";
import type { LocalConfidence, LocalMemoryEstimate, LocalVerdict } from "../types";

const MESSAGES = defineMessages({
  en: {
    weights: "Weights",
    kvCache: "KV cache",
    overhead: "Overhead",
    available: (gb: string) => `${gb} GB available`,
    confidence: {
      exact: "exact",
      good: "good estimate",
      rough: "rough estimate"
    } as Record<LocalConfidence, string>,
    verdict: {
      ok: "Fits",
      tight: "Tight",
      fail: "Does not fit",
      inconclusive: "Inconclusive",
      not_applicable: "Not applicable"
    } as Record<LocalVerdict, string>,
    range: (low: string, high: string) => `range ${low}–${high} GB`,
    total: (gb: string) => `${gb} GB total`
  },
  es: {
    weights: "Pesos",
    kvCache: "Caché KV",
    overhead: "Overhead",
    available: (gb: string) => `${gb} GB disponibles`,
    confidence: {
      exact: "exacta",
      good: "estimación buena",
      rough: "estimación aproximada"
    } as Record<LocalConfidence, string>,
    verdict: {
      ok: "Entra",
      tight: "Justa",
      fail: "No entra",
      inconclusive: "Inconcluso",
      not_applicable: "No aplica"
    } as Record<LocalVerdict, string>,
    range: (low: string, high: string) => `rango ${low}–${high} GB`,
    total: (gb: string) => `${gb} GB total`
  }
});

function fmt(value: number): string {
  return value.toFixed(1);
}

function verdictBadgeClass(verdict: LocalVerdict): string {
  if (verdict === "ok") return "badge ok";
  if (verdict === "tight") return "badge warn";
  if (verdict === "fail") return "badge error";
  return "badge neutral";
}

type MemoryBarProps = {
  estimate: LocalMemoryEstimate;
};

/**
 * Stacked weights / KV cache / overhead against the available memory.
 *
 * Green under the margin the backend already judged "ok", amber for "tight"
 * (under 10% margin), red when the configuration does not fit at all — the
 * bar never recomputes the verdict, it only renders the one `estimate`
 * already carries.
 */
export function MemoryBar({ estimate }: MemoryBarProps) {
  const m = useMessages(MESSAGES);
  const { breakdown, verdict, confidence, lever, notes } = estimate;
  const available = Math.max(breakdown.available_gb, breakdown.total_gb, 0.001);
  const segments: [string, number, string][] = [
    [m.weights, breakdown.weights_gb, "memory-bar-weights"],
    [m.kvCache, breakdown.kv_cache_gb, "memory-bar-kv"],
    [m.overhead, breakdown.overhead_gb, "memory-bar-overhead"]
  ];

  return (
    <div className={`memory-bar memory-bar--${verdict}`}>
      <div className="memory-bar-head">
        <span className={verdictBadgeClass(verdict)}>{m.verdict[verdict]}</span>
        <span className="badge neutral">{m.confidence[confidence]}</span>
        <span className="muted small">{m.total(fmt(breakdown.total_gb))}</span>
        <span className="muted small">{m.available(fmt(available))}</span>
      </div>
      <div className="memory-bar-track" role="img">
        {segments.map(([label, value, className]) => {
          const pct = Math.min(100, (value / available) * 100);
          return pct > 0 ? (
            <span
              className={className}
              key={label}
              style={{ width: `${pct}%` }}
              title={`${label}: ${fmt(value)} GB`}
            />
          ) : null;
        })}
      </div>
      <div className="memory-bar-legend">
        {segments.map(([label, value]) => (
          <span className="memory-bar-legend-item" key={label}>
            <span className="muted small">{label}</span> {fmt(value)} GB
          </span>
        ))}
      </div>
      {breakdown.low_gb !== null && breakdown.low_gb !== undefined && breakdown.high_gb !== null && breakdown.high_gb !== undefined ? (
        <p className="muted small">{m.range(fmt(breakdown.low_gb), fmt(breakdown.high_gb))}</p>
      ) : null}
      {lever ? <p className="muted small">{lever}</p> : null}
      {notes.map((note) => (
        <p className="muted small" key={note}>
          {note}
        </p>
      ))}
    </div>
  );
}
