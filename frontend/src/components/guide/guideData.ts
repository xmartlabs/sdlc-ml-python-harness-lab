import { api } from "../../api";
import type { CaseRecord, ModelOption } from "../../types";
import type { GuideEstimate, GuideOption } from "./GuideFlow";

/**
 * Shared data for the guided flows (ADR-0093): real models, real cases, and the
 * cost estimate from the lab's own medians. Nothing here is hard-coded per
 * flow, so a model added to the gateway or a case seeded later shows up in
 * every flow without a code change.
 */

let modelsPromise: Promise<ModelOption[]> | null = null;
let casesPromise: Promise<CaseRecord[]> | null = null;

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

/** A model id as a run records it: the gateway prefix is routing, not identity. */
export function runModelId(id: string): string {
  return id.startsWith("gateway/") ? id.slice("gateway/".length) : id;
}

/** Local models answer on this machine; everything else is a cloud call. */
export function isLocal(model: ModelOption): boolean {
  return /lmstudio|ollama/i.test(model.id);
}

export function modelOptions(models: ModelOption[], local: string, cloud: string): GuideOption[] {
  return models.map((model) => ({
    id: model.id,
    label: model.label || runModelId(model.id),
    sub: isLocal(model) ? local : cloud,
    tip: runModelId(model.id)
  }));
}

/** Median cost and minutes per run; null when nothing was measured yet. */
export async function perRun(
  caseId: string,
  modelId: string
): Promise<{ cost: number | null; minutes: number | null; n: number }> {
  try {
    // The id a run records is the one `armFor` creates it with: bare for
    // Claude models, the full `gateway/...` id otherwise. Strip nothing else,
    // or a gateway model never matches its own history.
    const row = await api.estimateRun(caseId, armFor(modelId).model);
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
  cells: { caseId: string; modelId: string; runs: number }[],
  arms: number,
  notes: string[] = []
): Promise<GuideEstimate> {
  const parts = await Promise.all(cells.map((cell) => perRun(cell.caseId, cell.modelId)));
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

/**
 * The harness that drives a model, as Compare models pairs them: claude for
 * Claude models (by their bare id), opencode for local and gateway models.
 */
export function armFor(modelId: string): { harness: "claude" | "opencode"; model: string } {
  const bare = runModelId(modelId);
  if (/^claude-/.test(bare)) return { harness: "claude", model: bare };
  return { harness: "opencode", model: modelId };
}

export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
}
