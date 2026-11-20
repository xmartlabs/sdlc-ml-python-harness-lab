import type { CaseRecord } from "./types";

/**
 * The family a case belongs to, for browsing (ADR-0090).
 *
 * Derived from what the cases API already returns — the case type, its tags and
 * its machine profile — rather than from a new stored field: `task.yaml`'s
 * `category` lumps security, review and containment in with `bugfix`, and the
 * tags already say which is which. Nothing here feeds a run, a hash or a score;
 * it only decides which shelf a case sits on.
 *
 * Order matters: the first rule that matches wins, so a 3D game that is also
 * tagged `frontier` lands in games, and a SWE-bench Pro instance is never
 * mistaken for a plain bugfix.
 */
export type CaseFamily =
  | "swe_bench"
  | "game_3d"
  | "security"
  | "web"
  | "build"
  | "other";

export const CASE_FAMILIES: readonly CaseFamily[] = [
  "swe_bench",
  "game_3d",
  "security",
  "web",
  "build",
  "other"
];

const SECURITY_TAGS = new Set(["security", "code-review", "containment", "safety"]);
const WEB_TAGS = new Set(["frontend", "browser"]);

export function caseFamily(item: CaseRecord): CaseFamily {
  const tags = new Set(item.tags ?? []);
  if (item.type === "swe_bench_instance" || item.type === "swe_pro_instance") {
    return "swe_bench";
  }
  if (tags.has("3d") || tags.has("godot")) {
    return "game_3d";
  }
  if ([...tags].some((tag) => SECURITY_TAGS.has(tag))) {
    return "security";
  }
  if ([...tags].some((tag) => WEB_TAGS.has(tag))) {
    return "web";
  }
  if (item.type === "build_from_scratch" && tags.size > 0) {
    return "build";
  }
  return "other";
}

/** Cases grouped by family, in `CASE_FAMILIES` order, each list sorted by id. */
export function groupByFamily(cases: CaseRecord[]): Record<CaseFamily, CaseRecord[]> {
  const out = Object.fromEntries(CASE_FAMILIES.map((family) => [family, []])) as unknown as Record<
    CaseFamily,
    CaseRecord[]
  >;
  for (const item of cases) {
    out[caseFamily(item)].push(item);
  }
  for (const family of CASE_FAMILIES) {
    out[family].sort((a, b) => a.case_id.localeCompare(b.case_id));
  }
  return out;
}

/** True when a case runs in its own image (ADR-0078) — the 3D cases today. */
export function needsOwnImage(item: CaseRecord): boolean {
  const meta = item.metadata_json ?? {};
  return typeof meta.runner_image === "string" && meta.runner_image !== "";
}

/** True when a finished run of this case can be played in the Play tab. */
export function isPlayable(item: CaseRecord): boolean {
  const meta = item.metadata_json ?? {};
  return typeof meta.play_command === "string" && meta.play_command !== "";
}
