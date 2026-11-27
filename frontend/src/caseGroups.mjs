// The shelf a case sits on in a guided flow's *Which case?* step.
//
// Coarser than `caseFamily` (caseFamilies.ts), which sorts the case library into
// six families: a flow only needs to tell what kind of work the case asks for.
// Derived from the tags the cases API already returns, never stored, and it feeds
// nothing but the layout — not a run, a hash or a score. Kept in plain JS so
// `node --test` covers it without a browser.
//
// The first rule that matches wins:
//   play                            → gameplay (the model plays a fixed game)
//   3d or godot                     → game_3d  (builds a game in its own image)
//   code-review or audit            → review   (reads and reports, changes nothing)
//   anything else, SWE-bench too    → code

/** The groups, in the order the step shows them. */
export const CASE_GROUPS = ["code", "review", "game_3d", "gameplay"];

const REVIEW_TAGS = ["code-review", "audit"];

/** The group of one case, from its tags. */
export function caseGroup(item) {
  const tags = new Set(item?.tags ?? []);
  if (tags.has("play")) return "gameplay";
  if (tags.has("3d") || tags.has("godot")) return "game_3d";
  if (REVIEW_TAGS.some((tag) => tags.has(tag))) return "review";
  return "code";
}

/**
 * The cases split by group, in `CASE_GROUPS` order, empty groups left out.
 * Within a group the input order is kept, so the step lists cases as before.
 */
export function groupCases(cases) {
  const buckets = new Map(CASE_GROUPS.map((group) => [group, []]));
  for (const item of cases) buckets.get(caseGroup(item)).push(item);
  return CASE_GROUPS.filter((group) => buckets.get(group).length > 0).map((group) => ({
    group,
    items: buckets.get(group)
  }));
}
