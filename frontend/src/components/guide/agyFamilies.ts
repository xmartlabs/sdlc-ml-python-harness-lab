/**
 * Antigravity CLI models as one card per family, with the effort picked apart.
 *
 * agy has no effort flag: the thinking level is the last part of the model id
 * (`gemini-3.8-flash-high`), and `agy models` lists every level as its own
 * model. Shown that way, one family took three or four cards. Grouped here,
 * the chooser shows the family once and an effort selector, like claude's,
 * and still sends the exact id agy accepts: the run, its condition hash and
 * the preflight are unchanged.
 *
 * No imports on purpose: it is plain data shaping, checked with Node alone.
 */

/** agy's thinking levels, lowest first (agy 1.5.14 `agy models`). */
export const AGY_LEVELS = ["low", "medium", "high", "xhigh"] as const;

/** The level every family offers, so no card starts disabled. */
export const AGY_DEFAULT_LEVEL = "high";

const LEVEL_SUFFIX = /-(xhigh|high|medium|low)$/;
const LABEL_LEVEL = /\s*\((?:xhigh|high|medium|low)\)$/i;

export type AgyModelRow = {
  id: string;
  label: string;
  generation: "current" | "previous";
};

export type AgyFamily = {
  /** The id without its level (`gemini-3.8-flash`). */
  key: string;
  /** The label without its level (`Gemini 3.8 Flash`). */
  label: string;
  generation: "current" | "previous";
  /** Level → the model id agy takes. `""` for an id with no level suffix. */
  slugs: Record<string, string>;
  /** The levels it offers, lowest first. */
  levels: string[];
};

/** The thinking level an agy model id ends in, or "" when it has none. */
export function agyLevelOf(slug: string): string {
  return LEVEL_SUFFIX.exec(slug)?.[1] ?? "";
}

/**
 * The catalog rows grouped by family, in the catalog's own order (current
 * families first). An id without a level suffix — a newer CLI's model the lab
 * has not seen — stays a family of its own that ignores the selector.
 */
export function agyFamilies(rows: AgyModelRow[]): AgyFamily[] {
  const families = new Map<string, AgyFamily>();
  for (const row of rows) {
    const level = agyLevelOf(row.id);
    const key = level ? row.id.slice(0, -(level.length + 1)) : row.id;
    let family = families.get(key);
    if (!family) {
      family = {
        key,
        label: row.label.replace(LABEL_LEVEL, ""),
        generation: row.generation,
        slugs: {},
        levels: []
      };
      families.set(key, family);
    }
    family.slugs[level] = row.id;
  }
  for (const family of families.values()) {
    const known = AGY_LEVELS.filter((level) => level in family.slugs);
    family.levels = known;
  }
  return [...families.values()];
}

/**
 * The model id to run for a family at a level: the family's own id when it
 * has no levels, `undefined` when it does not offer that one.
 */
export function agySlugFor(family: AgyFamily, level: string): string | undefined {
  return family.slugs[""] ?? family.slugs[level];
}

/** Every level some family offers, lowest first: the selector's buttons. */
export function agyLevelsOffered(families: AgyFamily[]): string[] {
  return AGY_LEVELS.filter((level) => families.some((family) => family.levels.includes(level)));
}
