import { defineMessages, pickMessages, useMessages } from "../i18n";
import type { CapabilityBlock, TierReading } from "../types";

/**
 * The capability frontier (ADR-0069): per arm, the highest difficulty tier it
 * does *reliably* — every case passing in at least 4 of 5 runs and the pooled
 * 95% lower bound at least 0.70 — with every lower tier reliable too. "Capable"
 * means it passed but not often enough to rely on; three runs can never show
 * more than that.
 */

const MESSAGES = defineMessages({
  en: {
    statusLabel: {
      reliable: "reliable",
      capable: "capable",
      not_established: "not yet"
    } as Record<TierReading["status"], string>,
    passingTooFewCases: "passing · too few cases",
    tooFewCases: "too few cases",
    title: "Capability frontier",
    intro: (
      <>
        Per arm, the highest tier done <strong>reliably</strong>: every case
        passing in at least 4 of 5 runs and the pooled 95% lower bound at
        least 70%, with every lower tier reliable too. <em>Capable</em> means
        it passed, but not often enough to rely on.
      </>
    ),
    everyTier: "Every tier",
    tier: (name: string) => `Tier ${name}`,
    shortfallWarning: (minCases: number) => (
      <>
        has fewer than {minCases} cases, so no frontier can be established
        yet. A frontier marked <strong>?</strong> is where the runs so far
        point; add cases tagged <code>tier:&lt;name&gt;</code> to turn it
        into a reading.
      </>
    ),
    arm: "Arm",
    frontier: "Frontier",
    tierHeader: (name: string) => `Tier ${name}`,
    tierWithQuestion: (name: string) => `Tier ${name}?`,
    noneYet: "none yet",
    footer:
      "Hover a cell for the reason. Failures from a broken model-harness conversation — a malformed tool call, a context overflow — are a serving or harness fix, not a limit of the model; check the run log before ruling a small model out."
  },
  es: {
    statusLabel: {
      reliable: "confiable",
      capable: "capaz",
      not_established: "todavía no"
    } as Record<TierReading["status"], string>,
    passingTooFewCases: "pasa · pocos casos",
    tooFewCases: "pocos casos",
    title: "Frontera de capacidad",
    intro: (
      <>
        Por brazo, el nivel más difícil hecho de forma <strong>confiable</strong>:
        todo caso que pasa en al menos 4 de 5 corridas y la cota inferior del
        95% agrupada de al menos 70%, con todo nivel inferior también
        confiable. <em>Capaz</em> significa que pasó, pero no con la
        frecuencia suficiente para confiar en eso.
      </>
    ),
    everyTier: "Todos los niveles",
    tier: (name: string) => `Nivel ${name}`,
    shortfallWarning: (minCases: number) => (
      <>
        tiene menos de {minCases} casos, así que todavía no se puede
        establecer una frontera. Una frontera marcada <strong>?</strong> es
        hacia dónde apuntan las corridas hasta ahora; agregá casos
        etiquetados <code>tier:&lt;nombre&gt;</code> para convertirla en una
        lectura.
      </>
    ),
    arm: "Brazo",
    frontier: "Frontera",
    tierHeader: (name: string) => `Nivel ${name}`,
    tierWithQuestion: (name: string) => `Nivel ${name}?`,
    noneYet: "ninguna aún",
    footer:
      "Pasá el mouse por una celda para ver la razón. Las fallas por una conversación rota entre modelo y arnés — un llamado a herramienta mal formado, un desborde de contexto — son un arreglo de serving o de arnés, no un límite del modelo; revisá el log de la corrida antes de descartar un modelo chico."
  }
});

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

/** A tier with too few cases reads "too few cases", plus whether its runs held up. */
function tierLabel(reading: TierReading): string {
  const m = pickMessages(MESSAGES);
  if (reading.cases < MIN_CASES) {
    return reading.runs_ok ? m.passingTooFewCases : m.tooFewCases;
  }
  return m.statusLabel[reading.status];
}

const STATUS_CLASS: Record<TierReading["status"], string> = {
  reliable: "badge ok",
  capable: "badge warn",
  not_established: "badge neutral"
};

function tierClass(reading: TierReading): string {
  if (reading.cases < MIN_CASES && reading.runs_ok) return "badge warn";
  return STATUS_CLASS[reading.status];
}

function frontierLabel(arm: CapabilityBlock["arms"][number]): string {
  const m = pickMessages(MESSAGES);
  if (arm.frontier !== null) return m.tierHeader(arm.frontier);
  if (Object.keys(arm.case_shortfall ?? {}).length && arm.frontier_if_enough_cases) {
    return m.tierWithQuestion(arm.frontier_if_enough_cases);
  }
  return m.noneYet;
}

const MIN_CASES = 4;

export function CapabilityPanel({ block }: { block: CapabilityBlock }) {
  const m = useMessages(MESSAGES);
  const short = block.tiers.filter((tier) =>
    block.arms.some((arm) => (arm.case_shortfall ?? {})[tier] > 0)
  );
  return (
    <div className="card capability">
      <h3>{m.title}</h3>
      <p className="muted small">{m.intro}</p>
      {short.length > 0 && (
        <div className="callout callout--warning small">
          <p>
            {short.length === block.tiers.length
              ? m.everyTier
              : short.map((name) => m.tier(name)).join(", ")}{" "}
            {m.shortfallWarning(MIN_CASES)}
          </p>
        </div>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>{m.arm}</th>
            <th>{m.frontier}</th>
            {block.tiers.map((tier) => (
              <th key={tier}>{m.tierHeader(tier)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.arms.map((arm) => (
            <tr key={arm.label}>
              <td className="mono small">{arm.label}</td>
              <td>
                <strong>{frontierLabel(arm)}</strong>
              </td>
              {block.tiers.map((tier) => {
                const reading = arm.tiers[tier];
                return (
                  <td key={tier} title={reading?.reason}>
                    {reading ? (
                      <>
                        <span className={tierClass(reading)}>{tierLabel(reading)}</span>{" "}
                        <span className="muted small">
                          {reading.passed}/{reading.runs} · ≥{pct(reading.wilson_low)}
                        </span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{m.footer}</p>
    </div>
  );
}
