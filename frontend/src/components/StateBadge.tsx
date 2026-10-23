import { defineMessages, useMessages } from "../i18n";
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

const MESSAGES = defineMessages<Record<RunState, string>>({
  en: {
    draft: "draft",
    pending: "pending",
    running: "running",
    awaiting_manual_score: "awaiting manual score",
    scoring: "scoring",
    auditing: "auditing",
    completed: "completed",
    failed: "failed",
    cancelled: "cancelled"
  },
  es: {
    draft: "borrador",
    pending: "pendiente",
    running: "corriendo",
    awaiting_manual_score: "esperando puntaje manual",
    scoring: "puntuando",
    auditing: "auditando",
    completed: "completada",
    failed: "falló",
    cancelled: "cancelada"
  }
});

type StateBadgeProps = {
  state: RunState;
};

export function StateBadge({ state }: StateBadgeProps) {
  const variant = VARIANT_BY_STATE[state] ?? "pending";
  const m = useMessages(MESSAGES);
  return <span className={`badge badge--${variant}`}>{m[state] ?? state}</span>;
}
