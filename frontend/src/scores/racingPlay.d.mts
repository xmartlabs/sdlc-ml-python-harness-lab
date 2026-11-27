export interface RacingScoreLike {
  name: string;
  value_num: number | null;
  value_bool: boolean | null;
  value_text: string | null;
  reason: string;
}
export interface RacingPlaySummary {
  mode: string;
  badge: string;
  outcome: string;
  impacts: string;
  complete: boolean;
}
export function racingPlaySummary(scores: RacingScoreLike[]): RacingPlaySummary | null;
