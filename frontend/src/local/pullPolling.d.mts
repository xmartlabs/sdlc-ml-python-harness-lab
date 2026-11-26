export type PollOutcome = { job: { status: "downloading" | "completed" | "failed" } } | { job?: undefined; status: number };
export type PollAction = "continue" | "done" | "stop" | "forget" | "retry" | "give_up";
export function pollDelay(failures: number, baseMs?: number, maxMs?: number): number;
export function nextPollStep(outcome: PollOutcome, failures: number, maxFailures?: number): { action: PollAction; failures: number };
export interface Latest { next(): number; isLatest(ticket: number): boolean; cancel(): void }
export function createLatest(): Latest;
