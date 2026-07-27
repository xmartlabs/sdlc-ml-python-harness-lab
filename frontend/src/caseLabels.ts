import type { CaseRecord } from "./types";

// Case types whose case_id is a long SWE-bench instance id (100+ chars), so the
// human-readable name should lead in listings, not the id.
export const SWE_CASE_TYPES = new Set(["swe_bench_instance", "swe_pro_instance"]);

// instance_ansible__ansible-<40-char sha>-v<sha> → ansible__ansible-5f4e332
export function shortInstanceLabel(instanceId: string): string {
  const bare = instanceId.replace(/^instance_/, "");
  return bare.replace(/-([0-9a-f]{7})[0-9a-f]{33}(-v.*)?$/, "-$1");
}

// Single-line label for a <option> / compact row: descriptive name first for
// SWE cases (with the short id as a suffix), plain "id · name" otherwise.
export function caseOptionLabel(item: CaseRecord): string {
  if (SWE_CASE_TYPES.has(item.type) && item.name && item.name !== item.case_id) {
    return `${item.name} · ${shortInstanceLabel(item.case_id)}`;
  }
  return item.name && item.name !== item.case_id
    ? `${item.case_id} · ${item.name}`
    : item.case_id;
}

// Headline for a case in a dense listing: the descriptive title when the case
// has one, else the (shortened) id. `fallbackId` covers runs whose case is no
// longer in the catalog.
export function caseDisplayName(item: CaseRecord | undefined, fallbackId: string): string {
  if (!item) {
    return fallbackId ? shortInstanceLabel(fallbackId) : "";
  }
  if (item.name && item.name !== item.case_id) {
    return item.name;
  }
  return shortInstanceLabel(item.case_id);
}

// success_criteria stores the test lists as JSON strings; we only show counts.
function parseTestCount(criteria: Record<string, string> | undefined, key: string): number | null {
  const raw = criteria?.[key];
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.length : null;
  } catch {
    return null;
  }
}

// Fail-to-pass / pass-to-pass counts for a SWE case, or null when the case does
// not carry test lists (non-SWE cases, or an unknown case).
export function caseTestCounts(item: CaseRecord | undefined): { f2p: number; p2p: number } | null {
  const f2p = parseTestCount(item?.success_criteria, "fail_to_pass");
  const p2p = parseTestCount(item?.success_criteria, "pass_to_pass");
  return f2p !== null && p2p !== null ? { f2p, p2p } : null;
}

// Run ids are `<case_id>-K-NNN` (ADR-0006), so the attempt is everything after
// the case id — "K-009". Falls back to a suffix match for legacy/custom labels.
export function runAttemptSuffix(runId: string, caseId: string): string {
  if (caseId && runId.startsWith(`${caseId}-`)) {
    return runId.slice(caseId.length + 1);
  }
  const match = runId.match(/-(K-\d+)$/);
  return match ? match[1] : "";
}
