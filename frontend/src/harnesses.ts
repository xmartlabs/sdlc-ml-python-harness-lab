import type { ModelOption } from "./types";

/**
 * The harness menu, shared by every form that launches runs.
 *
 * Three copies of this list had already diverged in spirit (the run form, the
 * batch form, the experiment wizard); a fourth in the experiment launch panel
 * was the point at which one source of truth became cheaper than the drift.
 */
export const HARNESS_OPTIONS = ["opencode", "codex", "claude", "antigravity"] as const;

// Harnesses that run via the host CLI (native auth) instead of the runner
// container — they need the host worker (subprocess backend).
export const HOST_HARNESSES = new Set(["codex", "claude"]);

// Native model menus for the host-CLI harnesses. Values are passed straight to
// the CLI's --model, so keep them to ids the CLI accepts. Edit these as new
// versions ship — this is the systematic-lab knob for host harnesses.
export const HARNESS_NATIVE_MODELS: Record<string, ModelOption[]> = {
  claude: [
    { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
    { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" }
  ],
  // Only the confirmed CLI default is listed — add your codex model ids here.
  codex: [{ id: "codex-mini-latest", label: "Codex Mini (latest)" }]
};
