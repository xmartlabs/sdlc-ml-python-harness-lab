export type CaseGroup = "code" | "review" | "game_3d" | "gameplay";
export const CASE_GROUPS: readonly CaseGroup[];
export function caseGroup(item: { tags?: string[] } | null | undefined): CaseGroup;
export function groupCases<T extends { tags?: string[] }>(cases: T[]): { group: CaseGroup; items: T[] }[];
