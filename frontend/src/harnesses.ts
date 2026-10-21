import { useEffect, useState } from "react";

import { api } from "./api";
import type { ModelOption } from "./types";

/**
 * The harness menu, shared by every form that launches runs.
 *
 * Three copies of this list had already diverged in spirit (the run form, the
 * batch form, the experiment wizard); a fourth in the experiment launch panel
 * was the point at which one source of truth became cheaper than the drift.
 */
// bench_run.py accepts opencode | codex | claude; antigravity is the manual `make bench-antigravity` flow.
export const HARNESS_OPTIONS = ["opencode", "codex", "claude"] as const;

// Harnesses that run via the host CLI (native auth) instead of the runner
// container — they need the host worker (subprocess backend).
export const HOST_HARNESSES = new Set(["codex", "claude"]);

// Native model menus for the host-CLI harnesses. Values are passed straight to
// the CLI's --model, so keep them to ids the CLI accepts. Edit these as new
// versions ship — this is the systematic-lab knob for host harnesses.
export const HARNESS_NATIVE_MODELS: Record<string, ModelOption[]> = {
  // Current generation first (cached 2026-10-06); the previous ones stay so new
  // runs can be compared with the archive, which is mostly Haiku 4.5.
  claude: [
    { id: "claude-opus-5-5", label: "Claude Opus 5.5" },
    { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5" },
    { id: "claude-haiku-5-5", label: "Claude Haiku 5.5" },
    { id: "claude-fable-5-1", label: "Claude Fable 5.1" },
    { id: "claude-sonnet-5", label: "Claude Sonnet 5 (previous)" },
    { id: "claude-opus-4-8", label: "Claude Opus 4.8 (previous)" },
    { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 (previous)" }
  ],
  // Only the confirmed CLI default is listed — add your codex model ids here.
  codex: [{ id: "codex-mini-latest", label: "Codex Mini (latest)" }]
};

/** One native model as `GET /api/models/native` serves it (ADR-0069). */
export type NativeModel = ModelOption & {
  generation: "current" | "previous";
  size_rank: number;
  /** True/false when a gateway is configured; null when there is none to ask. */
  on_gateway: boolean | null;
};

/**
 * The native model menus from the backend — the single list a test holds to the
 * gateway — falling back to `HARNESS_NATIVE_MODELS` offline. A model the gateway
 * has no card for is labelled, so it is never picked by surprise.
 */
export function useNativeModels(): Record<string, NativeModel[]> {
  const fallback = Object.fromEntries(
    Object.entries(HARNESS_NATIVE_MODELS).map(([harness, models]) => [
      harness,
      models.map((model, index) => ({
        ...model,
        generation: model.label.includes("(previous)") ? "previous" : "current",
        size_rank: index + 1,
        on_gateway: null
      }))
    ])
  ) as Record<string, NativeModel[]>;
  const [models, setModels] = useState<Record<string, NativeModel[]>>(fallback);
  useEffect(() => {
    let active = true;
    api
      .listNativeModels()
      .then((catalog) => {
        if (!active) return;
        setModels(
          Object.fromEntries(
            Object.entries(catalog).map(([harness, rows]) => [
              harness,
              rows.map((row) => ({
                ...row,
                label:
                  row.label +
                  (row.generation === "previous" ? " (previous)" : "") +
                  (row.on_gateway === false ? " — not on your gateway" : "")
              }))
            ])
          )
        );
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);
  return models;
}
