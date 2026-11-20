import { api } from "../../api";
import { HARNESS_NATIVE_MODELS } from "../../harnesses";
import type { CaseRecord, ModelOption, NativeCliStatus, NativeModelRecord } from "../../types";
import type { GuideEstimate } from "./GuideFlow";

/**
 * Shared data for the guided flows (ADR-0093): real models, real cases, and the
 * cost estimate from the lab's own medians. Nothing here is hard-coded per
 * flow, so a model added to the gateway or a case seeded later shows up in
 * every flow without a code change.
 */

let modelsPromise: Promise<ModelOption[]> | null = null;
let casesPromise: Promise<CaseRecord[]> | null = null;
let nativePromise: Promise<Record<string, NativeModelRecord[]>> | null = null;
let clisPromise: Promise<NativeCliStatus[] | null> | null = null;

/** The four agent CLIs a guided run can use, in the order the picker shows them. */
export const GUIDE_HARNESSES = ["opencode", "claude", "codex", "gemini"] as const;
export type Harness = (typeof GUIDE_HARNESSES)[number];

export function loadModels(): Promise<ModelOption[]> {
  if (!modelsPromise) {
    modelsPromise = api
      .listModels()
      .then((catalog) => catalog.models.filter((model) => model.routable !== false))
      .catch(() => {
        modelsPromise = null;
        return [];
      });
  }
  return modelsPromise;
}

export function loadCases(): Promise<CaseRecord[]> {
  if (!casesPromise) {
    casesPromise = api
      .listCases()
      .then((rows) => rows.filter((row) => row.status === "active"))
      .catch(() => {
        casesPromise = null;
        return [];
      });
  }
  return casesPromise;
}

/**
 * The native model menus of claude, codex and gemini (ADR-0069), with their raw
 * labels; the offline fallback is the list the run form ships with.
 */
export function loadNative(): Promise<Record<string, NativeModelRecord[]>> {
  if (!nativePromise) {
    nativePromise = api.listNativeModels().catch(() => {
      nativePromise = null;
      return Object.fromEntries(
        Object.entries(HARNESS_NATIVE_MODELS).map(([harness, rows]) => [
          harness,
          rows.map((row, index) => ({
            id: row.id,
            label: row.label.replace(" (previous)", ""),
            generation: row.label.includes("(previous)") ? ("previous" as const) : ("current" as const),
            size_rank: index + 1,
            on_gateway: null
          }))
        ])
      );
    });
  }
  return nativePromise;
}

/** Whether each agent CLI is installed and signed in; null when nobody answered. */
export function loadClis(): Promise<NativeCliStatus[] | null> {
  if (!clisPromise) {
    clisPromise = api
      .listNativeClis()
      .then((result) => result.clis)
      .catch(() => {
        clisPromise = null;
        return null;
      });
  }
  return clisPromise;
}

/** A model id as a run records it: the gateway prefix is routing, not identity. */
export function runModelId(id: string): string {
  return id.startsWith("gateway/") ? id.slice("gateway/".length) : id;
}

/** Median cost and minutes per run; null when nothing was measured yet. */
export async function perRun(
  caseId: string,
  modelId: string,
  level = ""
): Promise<{ cost: number | null; minutes: number | null; n: number }> {
  try {
    // The id a run records is the one `armFor` creates it with: bare for
    // Claude models, the full `gateway/...` id otherwise. Strip nothing else,
    // or a gateway model never matches its own history.
    const row = await api.estimateRun(caseId, armFor(modelId).model, level);
    return { cost: row.cost_usd ?? null, minutes: row.minutes ?? null, n: row.n };
  } catch {
    return { cost: null, minutes: null, n: 0 };
  }
}

/**
 * The estimate for `cells` (case, model, runs) triples: summed cost and time,
 * or null cost when any cell has no measured run — a partial sum would read
 * as a full one.
 */
export async function estimateCells(
  cells: { caseId: string; modelId: string; runs: number; level?: string }[],
  arms: number,
  notes: string[] = []
): Promise<GuideEstimate> {
  const parts = await Promise.all(cells.map((cell) => perRun(cell.caseId, cell.modelId, cell.level ?? "")));
  const runs = cells.reduce((sum, cell) => sum + cell.runs, 0);
  const known = parts.every((part) => part.cost !== null);
  const cost = known ? parts.reduce((sum, part, i) => sum + (part.cost ?? 0) * cells[i].runs, 0) : null;
  const minutesKnown = parts.every((part) => part.minutes !== null);
  const minutes = minutesKnown
    ? parts.reduce((sum, part, i) => sum + (part.minutes ?? 0) * cells[i].runs, 0)
    : null;
  return {
    arms,
    runs,
    costUsd: cost,
    minutes,
    basis: parts.length ? Math.min(...parts.map((part) => part.n)) : 0,
    notes
  };
}

export const IS_3D = (caseId: string) => /3d-v\d+$/.test(caseId);

/** A unique experiment id: `base`, or `base-N` when taken (slug max 64). */
export async function uniqueExperimentId(base: string): Promise<string> {
  const slug = base.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/-+/g, "-").slice(0, 64);
  const taken = new Set(
    await api
      .listExperiments()
      .then((rows) => rows.map((row) => row.experiment_id))
      .catch(() => [] as string[])
  );
  let id = slug;
  for (let n = 2; taken.has(id) && n < 100; n += 1) id = `${slug.slice(0, 60)}-${n}`;
  return id;
}

/** The three harness levels of the lab (ADR-0085), as a run spells them. */
export type HarnessLevel = "bare" | "minimal" | "full";

/**
 * The run fields that make a harness level — the same spelling the server's
 * ladder uses (`harness_levels.level_overrides`), so a level picked here and a
 * rung there are one condition. `minimalDisabled` is the minimal profile's
 * ablation from `/api/harness/profiles`.
 */
export function levelOverrides(level: HarnessLevel, minimalDisabled: string[]): Record<string, unknown> {
  if (level === "bare") return { harness_source: "bare" };
  if (level === "minimal") return { harness_source: "lab_root", skills_disabled: [...minimalDisabled].sort() };
  return { harness_source: "lab_root", skills_disabled: [] };
}

/**
 * One answer holds an arm's harness, model and, for claude, its reasoning
 * effort (ADR-0081): `claude|claude-opus-5-5` or `claude|claude-opus-5-5|high`.
 */
export function encodeArm(harness: Harness, model: string, effort = ""): string {
  return effort ? `${harness}|${model}|${effort}` : `${harness}|${model}`;
}

/**
 * The harness and model of an answer. The guided flows pick both (harness
 * first, then one of its models), so the answer says which; a bare model id —
 * an older link — falls back to the pairing Compare models uses: claude for
 * Claude models, opencode for local and gateway ones.
 */
export function armFor(value: string): { harness: Harness; model: string; effort: string } {
  const parts = value.split("|");
  if (parts.length >= 2 && (GUIDE_HARNESSES as readonly string[]).includes(parts[0])) {
    return { harness: parts[0] as Harness, model: parts[1], effort: parts[2] ?? "" };
  }
  const bare = runModelId(value);
  if (/^claude-/.test(bare)) return { harness: "claude", model: bare, effort: "" };
  return { harness: "opencode", model: value, effort: "" };
}

/** The prompt a plain run of this case uses: `default` when it declares one. */
export function defaultVariant(row: CaseRecord | undefined): string {
  const variants = Object.keys(row?.prompt_variants ?? {});
  return variants.includes("default") ? "default" : (variants[0] ?? "default");
}

/**
 * Why a launch of these arms would be refused, or "" when the server sees no
 * problem. Each distinct arm goes through the same `POST /api/runs/preflight`
 * the launch runs, with its harness level, so a missing case image, a CLI that
 * is not signed in or a model the gateway does not serve shows on the review
 * screen — not as a failed run a second after launching. A preflight that
 * cannot be reached says nothing: unknown is not refused.
 */
export async function launchBlocker(
  caseRow: CaseRecord | undefined,
  values: string[],
  pinned: Record<string, unknown> = {}
): Promise<string> {
  if (!caseRow) return "";
  for (const value of [...new Set(values)]) {
    const arm = armFor(value);
    try {
      const result = await api.preflightRunConfig({
        case_id: caseRow.case_id,
        prompt_variant: defaultVariant(caseRow),
        harness: arm.harness,
        planner_model: null,
        executor_model: arm.model,
        executor_effort: arm.effort,
        score_mode: "manual",
        max_steps: caseRow.max_steps ?? 5,
        resume: false,
        dry_run: false,
        ...pinned
      });
      if (!result.ok) return result.detail;
    } catch {
      // The preflight endpoint itself failed: the launch will say why.
    }
  }
  return "";
}

/**
 * Launch experiments a flow just created, with the settings every arm shares:
 * the case's own steps and prompt, scored as usual. Each arm's harness and
 * model are pinned in its overrides, which win over these. The server runs the
 * same preflight as the Experiments page, so a run that cannot start is refused
 * here, not after it is queued. Effort is never shared: it lives in each
 * arm, or an opencode arm would inherit a claude-only flag and be refused.
 */
export async function launchExperiments(
  ids: string[],
  caseRow: CaseRecord | undefined,
  value: string,
  parallel: 1 | 2 = 1
): Promise<number> {
  if (!caseRow) throw new Error("Unknown case: reload the page and pick it again.");
  const arm = armFor(value);
  let count = 0;
  for (const id of ids) {
    try {
      const result = await api.launchExperiment(
        id,
        {
          case_id: caseRow.case_id,
          prompt_variant: defaultVariant(caseRow),
          harness: arm.harness,
          planner_model: null,
          executor_model: arm.model,
          score_mode: "manual",
          max_steps: caseRow.max_steps ?? 5,
          resume: false,
          dry_run: false
        },
        parallel
      );
      count += result.count;
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      throw new Error(count ? `${count} runs launched; ${id} was refused: ${reason}` : reason);
    }
  }
  return count;
}

export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
}
