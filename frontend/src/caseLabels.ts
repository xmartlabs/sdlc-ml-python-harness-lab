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
