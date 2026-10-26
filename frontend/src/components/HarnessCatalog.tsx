import { useMemo } from "react";
import { defineMessages, useMessages } from "../i18n";
import type { AttributionRecord, AuditFinding, AuditRecord, RunMetadata } from "../types";

type HarnessCatalogProps = {
  metadata: RunMetadata | null;
  attribution: AttributionRecord | null;
  audit: AuditRecord | null;
};

type CatalogRow = {
  name: string;
  kind: "skill" | "doc" | "hook" | "subagent";
  observed: "consulted" | "not-observed" | "unknown";
  disabled: boolean;
  experimental: boolean;
  finding: AuditFinding | null;
};

const KIND_ORDER: CatalogRow["kind"][] = ["skill", "subagent", "doc", "hook"];

const MESSAGES = defineMessages({
  en: {
    kindLabel: {
      skill: "Skills",
      subagent: "Subagents",
      doc: "Governance docs",
      hook: "Hooks"
    } as Record<CatalogRow["kind"], string>,
    verdictLabel: {
      useful: "useful",
      redundant: "redundant",
      improve: "needs work",
      unknown: "no evidence"
    } as Record<AuditFinding["verdict"], string>,
    noMetadata:
      "No metadata.json for this run yet (it is written when the run starts executing).",
    disabledThisRun: " (disabled this run)",
    experimental: " (experimental)",
    off: "off",
    consulted: "consulted",
    notObserved: "not observed",
    noVerdict: "no verdict",
    evidenceLabel: "evidence:",
    footnote:
      '"Consulted" is a lower bound from the step logs (skills are context-injected, so ' +
      "only explicit SKILL.md reads are observable). Verdicts come from the LLM audit's " +
      "structured pass — run the audit to populate them.",
    findingsNote: (parseError: string) => ` Findings note: ${parseError}`
  },
  es: {
    kindLabel: {
      skill: "Skills",
      subagent: "Subagentes",
      doc: "Documentos de gobernanza",
      hook: "Hooks"
    } as Record<CatalogRow["kind"], string>,
    verdictLabel: {
      useful: "útil",
      redundant: "redundante",
      improve: "necesita trabajo",
      unknown: "sin evidencia"
    } as Record<AuditFinding["verdict"], string>,
    noMetadata:
      "Todavía no hay metadata.json para esta corrida (se escribe cuando la corrida " +
      "empieza a ejecutarse).",
    disabledThisRun: " (deshabilitado en esta corrida)",
    experimental: " (experimental)",
    off: "apagado",
    consulted: "consultado",
    notObserved: "no observado",
    noVerdict: "sin veredicto",
    evidenceLabel: "evidencia:",
    footnote:
      '"Consultado" es una cota inferior a partir de los logs de pasos (los skills se ' +
      "inyectan por contexto, así que solo las lecturas explícitas de SKILL.md son " +
      "observables). Los veredictos vienen del paso estructurado de la auditoría LLM — " +
      "corré la auditoría para completarlos.",
    findingsNote: (parseError: string) => ` Nota de hallazgos: ${parseError}`
  }
});

function verdictBadgeClass(verdict: AuditFinding["verdict"]): string {
  if (verdict === "useful") return "badge ok";
  if (verdict === "improve") return "badge error";
  if (verdict === "redundant") return "badge neutral";
  return "badge";
}

// A doc appears in attribution as a path suffix (e.g. ".github/standards.md");
// the fingerprint keys are the same relative paths, so suffix-match both ways.
function docWasRead(name: string, readDocs: string[]): boolean {
  return readDocs.some((doc) => doc.endsWith(name) || name.endsWith(doc));
}

export function HarnessCatalog({ metadata, attribution, audit }: HarnessCatalogProps) {
  const m = useMessages(MESSAGES);
  const rows = useMemo<CatalogRow[]>(() => {
    if (!metadata) {
      return [];
    }
    const findingsByName = new Map<string, AuditFinding>();
    for (const item of audit?.findings_json?.items ?? []) {
      findingsByName.set(item.name, item);
    }
    const consultedSkills = new Set(attribution?.harness_usage?.skills_consulted ?? []);
    const readDocs = attribution?.harness_usage?.governance_docs_read ?? [];
    const disabledSkills = new Set(metadata.skills_disabled ?? []);
    const experimentalSkills = new Set(metadata.skills_experimental ?? []);
    const result: CatalogRow[] = [];

    for (const name of metadata.skills_bundle ?? []) {
      result.push({
        name,
        kind: "skill",
        observed: attribution ? (consultedSkills.has(name) ? "consulted" : "not-observed") : "unknown",
        disabled: false,
        experimental: experimentalSkills.has(name),
        finding: findingsByName.get(name) ?? null
      });
    }
    for (const name of disabledSkills) {
      result.push({
        name,
        kind: "skill",
        observed: "unknown",
        disabled: true,
        experimental: false,
        finding: null
      });
    }
    for (const name of metadata.subagents ?? []) {
      result.push({
        name,
        kind: "subagent",
        observed: "unknown",
        disabled: false,
        experimental: false,
        finding: findingsByName.get(name) ?? null
      });
    }
    for (const name of Object.keys(metadata.harness_fingerprint?.governance_docs ?? {})) {
      result.push({
        name,
        kind: "doc",
        observed: attribution ? (docWasRead(name, readDocs) ? "consulted" : "not-observed") : "unknown",
        disabled: false,
        experimental: false,
        finding: findingsByName.get(name) ?? null
      });
    }
    for (const name of Object.keys(metadata.harness_fingerprint?.hooks ?? {})) {
      result.push({
        name,
        kind: "hook",
        observed: "unknown",
        disabled: false,
        experimental: false,
        finding: findingsByName.get(name) ?? null
      });
    }
    return result;
  }, [metadata, attribution, audit]);

  if (!metadata) {
    return <p className="muted">{m.noMetadata}</p>;
  }

  const parseError = audit?.findings_json?.parse_error;

  return (
    <div className="harness-catalog">
      {KIND_ORDER.map((kind) => {
        const group = rows.filter((row) => row.kind === kind);
        if (group.length === 0) {
          return null;
        }
        return (
          <div className="harness-catalog-group" key={kind}>
            <p className="sidebar-label">{m.kindLabel[kind]}</p>
            <div className="table">
              {group.map((row) => (
                <div
                  className={row.disabled ? "table-row harness-row disabled" : "table-row harness-row"}
                  key={`${row.kind}:${row.name}`}
                >
                  <div className="harness-row-head">
                    <span className="harness-name">
                      <strong>{row.name}</strong>
                      {row.disabled ? <small>{m.disabledThisRun}</small> : null}
                      {row.experimental ? <small>{m.experimental}</small> : null}
                    </span>
                    <span>
                      {row.disabled ? (
                        <span className="badge">{m.off}</span>
                      ) : row.observed === "consulted" ? (
                        <span className="badge ok">{m.consulted}</span>
                      ) : row.observed === "not-observed" ? (
                        <span className="badge">{m.notObserved}</span>
                      ) : (
                        <span className="badge">—</span>
                      )}
                    </span>
                    <span className="harness-verdict">
                      {row.finding ? (
                        <span className={verdictBadgeClass(row.finding.verdict)}>
                          {m.verdictLabel[row.finding.verdict]}
                        </span>
                      ) : (
                        <span className="muted">{m.noVerdict}</span>
                      )}
                    </span>
                  </div>
                  {row.finding?.note ? (
                    <p className="harness-note">{row.finding.note}</p>
                  ) : null}
                  {row.finding?.used_evidence ? (
                    <p className="harness-evidence">
                      <span className="harness-evidence-label">{m.evidenceLabel}</span>{" "}
                      {row.finding.used_evidence}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        );
      })}
      <p className="muted">
        {m.footnote}
        {/* parseError is backend-composed and stays in English. */}
        {parseError ? m.findingsNote(parseError) : ""}
      </p>
    </div>
  );
}
