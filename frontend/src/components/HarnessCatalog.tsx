import { useMemo } from "react";
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
const KIND_LABEL: Record<CatalogRow["kind"], string> = {
  skill: "Skills",
  subagent: "Subagents",
  doc: "Governance docs",
  hook: "Hooks"
};

const VERDICT_LABEL: Record<AuditFinding["verdict"], string> = {
  useful: "útil",
  redundant: "redundante",
  improve: "a mejorar",
  unknown: "sin evidencia"
};

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
    return (
      <p className="muted">
        No metadata.json for this run yet (it is written when the run starts executing).
      </p>
    );
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
            <p className="sidebar-label">{KIND_LABEL[kind]}</p>
            <div className="table">
              {group.map((row) => (
                <div
                  className={row.disabled ? "table-row harness-row disabled" : "table-row harness-row"}
                  key={`${row.kind}:${row.name}`}
                >
                  <div className="harness-row-head">
                    <span className="harness-name">
                      <strong>{row.name}</strong>
                      {row.disabled ? <small> (disabled this run)</small> : null}
                      {row.experimental ? <small> (experimental)</small> : null}
                    </span>
                    <span>
                      {row.disabled ? (
                        <span className="badge">off</span>
                      ) : row.observed === "consulted" ? (
                        <span className="badge ok">consulted</span>
                      ) : row.observed === "not-observed" ? (
                        <span className="badge">not observed</span>
                      ) : (
                        <span className="badge">—</span>
                      )}
                    </span>
                    <span className="harness-verdict">
                      {row.finding ? (
                        <span className={verdictBadgeClass(row.finding.verdict)}>
                          {VERDICT_LABEL[row.finding.verdict]}
                        </span>
                      ) : (
                        <span className="muted">no verdict</span>
                      )}
                    </span>
                  </div>
                  {row.finding?.note ? (
                    <p className="harness-note">{row.finding.note}</p>
                  ) : null}
                  {row.finding?.used_evidence ? (
                    <p className="harness-evidence">
                      <span className="harness-evidence-label">evidence:</span>{" "}
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
        "Consulted" is a lower bound from the step logs (skills are context-injected, so only
        explicit SKILL.md reads are observable). Verdicts come from the LLM audit's structured
        pass — run the audit to populate them.
        {parseError ? ` Findings note: ${parseError}` : ""}
      </p>
    </div>
  );
}
