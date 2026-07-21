import type { RunState } from "../types";

type BadgeVariant = "pending" | "running" | "awaiting" | "completed" | "failed" | "cancelled";

const VARIANT_BY_STATE: Record<RunState, BadgeVariant> = {
  draft: "pending",
  pending: "pending",
  running: "running",
  awaiting_manual_score: "awaiting",
  scoring: "running",
  auditing: "running",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled"
};

type StateBadgeProps = {
  state: RunState;
};

export function StateBadge({ state }: StateBadgeProps) {
  const variant = VARIANT_BY_STATE[state] ?? "pending";
  return <span className={`badge badge--${variant}`}>{state}</span>;
}
