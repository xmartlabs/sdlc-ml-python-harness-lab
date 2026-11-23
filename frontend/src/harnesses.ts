import { useEffect, useState } from "react";

import { api } from "./api";
import { defineMessages, useLang } from "./i18n";
import type { ModelOption } from "./types";

/**
 * The harness menu, shared by every form that launches runs.
 *
 * Three copies of this list had already diverged in spirit (the run form, the
 * batch form, the experiment wizard); a fourth in the experiment launch panel
 * was the point at which one source of truth became cheaper than the drift.
 */
// bench_run.py accepts opencode | codex | claude | gemini | antigravity_cli.
// antigravity_cli is the automated Antigravity CLI (`agy`) on a Gemini API key
// (ADR-0096); `antigravity` (the IDE) stays the manual `make bench-antigravity`
// flow and is not offered here.
export const HARNESS_OPTIONS = ["opencode", "codex", "claude", "gemini", "antigravity_cli"] as const;

/** How a harness reads in a menu: the CLI's product name. */
export const HARNESS_LABELS: Record<string, string> = {
  opencode: "opencode",
  codex: "codex",
  claude: "claude",
  gemini: "gemini",
  antigravity_cli: "Antigravity CLI"
};

/** A harness id for display; unknown ids read as themselves. */
export function harnessLabel(harness: string): string {
  return HARNESS_LABELS[harness] ?? harness;
}

/**
 * The harness as it reads inside "Runs the … CLI": its label without a
 * trailing " CLI", so "Antigravity CLI" never reads "Antigravity CLI CLI".
 */
export function harnessCliName(harness: string): string {
  return harnessLabel(harness).replace(/ CLI$/, "");
}

// Harnesses that drive their own CLI with native auth instead of a gateway
// model. They run in the runner container (Docker) or on the host worker
// (subprocess backend); the model menu is the CLI's own. antigravity_cli runs
// in Docker only (the runner container is its sandbox).
export const HOST_HARNESSES = new Set(["codex", "claude", "gemini", "antigravity_cli"]);

// Native CLIs signed in with a subscription kept per provider in the lab's
// session store (ADR-0094): `make auth-codex` / `make auth-gemini`.
export const SUBSCRIPTION_HARNESSES = new Set(["codex", "gemini"]);

// Native CLIs authenticated by an API key the operator stores from System →
// Agent CLIs (ADR-0096). Per-token API billing, no device login.
export const API_KEY_HARNESSES = new Set(["antigravity_cli"]);

/**
 * Reasoning effort levels the claude CLI accepts through `--effort`, lowest
 * first (ADR-0081). Mirrors `native_models.EFFORT_LEVELS`; the backend catalog
 * says which of them each model accepts, and this is the offline fallback.
 */
export const EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"] as const;

/** Native models with no effort control; every other native claude model takes every level. */
const NO_EFFORT_MODELS = new Set(["claude-haiku-4-5-20251001"]);

// Native model menus for the host-CLI harnesses. Values are passed straight to
// the CLI's --model, so keep them to ids the CLI accepts. Edit these as new
// versions ship — this is the systematic-lab knob for host harnesses.
export const HARNESS_NATIVE_MODELS: Record<string, ModelOption[]> = {
  // Current generation first (cached 2026-11-15); the previous ones stay so new
  // runs can be compared with the archive, which is mostly Haiku 4.5.
  claude: [
    { id: "claude-opus-5-5", label: "Claude Opus 5.5" },
    { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5" },
    { id: "claude-haiku-5-5", label: "Claude Haiku 5.5" },
    { id: "claude-fable-5-2", label: "Claude Fable 5.2" },
    { id: "claude-fable-5-1", label: "Claude Fable 5.1 (previous)" },
    { id: "claude-sonnet-5", label: "Claude Sonnet 5 (previous)" },
    { id: "claude-opus-4-8", label: "Claude Opus 4.8 (previous)" },
    { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 (previous)" }
  ],
  // Offline fallback of native_models.NATIVE_MODELS (the API serves the live
  // list). codex ids: `codex debug models --bundled` on codex-cli 0.171.0;
  // gemini ids: `gemini --list-models` on gemini-cli 0.70.0. Leaving the model
  // empty keeps the CLI's own default.
  codex: [
    { id: "gpt-6.2-sol", label: "GPT-6.2-Sol" },
    { id: "gpt-6.2-astra", label: "GPT-6.2-Astra" },
    { id: "gpt-6.1-terra", label: "GPT-6.1 Terra" },
    { id: "gpt-6.1-sol", label: "GPT-6.1-Sol (previous)" },
    { id: "gpt-6-sol", label: "GPT-6-Sol (previous)" },
    { id: "gpt-6-astra", label: "GPT-6-Astra (previous)" },
    { id: "gpt-6-luna", label: "GPT-6-Luna (previous)" },
    { id: "gpt-5.5", label: "GPT-5.5 (previous)" }
  ],
  gemini: [
    { id: "auto", label: "Auto (CLI routes Pro/Flash)" },
    { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
    { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
    { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash-Lite" }
  ],
  // Antigravity CLI on a Gemini API key (ADR-0096): `agy models` on agy 1.5.14,
  // Gemini API provider. A slug is a model and a thinking level; the CLI has no
  // default the lab can name, so a run always picks one (first = form default).
  antigravity_cli: [
    { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)" },
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash (High)" },
    { id: "gemini-3.8-flash-low", label: "Gemini 3.8 Flash (Low)" },
    { id: "gemini-4-neon-high", label: "Gemini 4 Neon (High)" },
    { id: "gemini-4-neon-xhigh", label: "Gemini 4 Neon (XHigh)" },
    { id: "gemini-4-carbon-high", label: "Gemini 4 Carbon (High)" },
    { id: "gemini-4-argon-high", label: "Gemini 4 Argon (High)" },
    { id: "gemini-4-hydrogen-high", label: "Gemini 4 Hydrogen (High)" },
    { id: "gemini-3.1-pro-high", label: "Gemini 3.1 Pro (High)" }
  ]
};

const MESSAGES = defineMessages({
  en: { previous: " (previous)", notOnGateway: " — not on your gateway" },
  es: { previous: " (anterior)", notOnGateway: " — no está en tu gateway" }
});

/**
 * The live reading of every native model by id, across harnesses: its label
 * with the generation and gateway suffixes, and whether the gateway serves it.
 * Forms that still list `HARNESS_NATIVE_MODELS` read it so a model the gateway
 * cannot serve is marked everywhere, not only in the harness x model form.
 */
export function useNativeModelIndex(): Record<string, NativeModel> {
  const catalog = useNativeModels();
  return Object.fromEntries(
    Object.values(catalog)
      .flat()
      .map((model) => [model.id, model])
  );
}

/** The first native model the gateway does not rule out, for a harness default. */
export function defaultNativeModel(
  harness: string,
  index: Record<string, NativeModel>
): string | undefined {
  const models = HARNESS_NATIVE_MODELS[harness];
  if (!models) return undefined;
  const served = models.find((model) => index[model.id]?.on_gateway !== false);
  return (served ?? models[0]).id;
}

/** One native model as `GET /api/models/native` serves it (ADR-0069). */
export type NativeModel = ModelOption & {
  generation: "current" | "previous";
  size_rank: number;
  /** True/false when a gateway is configured; null when there is none to ask. */
  on_gateway: boolean | null;
  /** Reasoning effort levels the model accepts (ADR-0081); empty = none. */
  effort_levels: string[];
};

/**
 * The effort levels a run with this harness and executor model can be given.
 * Empty means the selector is not offered: effort is a claude CLI flag, and a
 * gateway-routed or effort-less model has nowhere to send it.
 */
export function effortLevelsFor(
  harness: string,
  model: string,
  index: Record<string, NativeModel>
): string[] {
  if (harness !== "claude" || !model || model.includes("/")) return [];
  const known = index[model];
  if (known) return known.effort_levels;
  // A hand-typed id the catalog does not know: the CLI decides, so offer all.
  return [...EFFORT_LEVELS];
}

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
        on_gateway: null,
        effort_levels:
          harness === "claude" && !NO_EFFORT_MODELS.has(model.id) ? [...EFFORT_LEVELS] : []
      }))
    ])
  ) as Record<string, NativeModel[]>;
  const [models, setModels] = useState<Record<string, NativeModel[]>>(fallback);
  const m = MESSAGES[useLang().lang];
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
                // An older API without the field: same rule as the fallback.
                effort_levels:
                  row.effort_levels ??
                  (harness === "claude" && !NO_EFFORT_MODELS.has(row.id) ? [...EFFORT_LEVELS] : []),
                label:
                  row.label +
                  (row.generation === "previous" ? m.previous : "") +
                  (row.on_gateway === false ? m.notOnGateway : "")
              }))
            ])
          )
        );
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [m]);
  return models;
}
