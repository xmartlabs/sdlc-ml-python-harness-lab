import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { IconChevronRight, IconSearch } from "./icons";

export type PromptPreviewEntry = {
  caseId: string;
  label: string;
};

type PromptPreviewProps = {
  entries: PromptPreviewEntry[];
  variant: string;
};

type LoadState =
  | { status: "idle" | "loading" }
  | { status: "error"; message: string }
  | { status: "loaded"; items: { label: string; content: string }[] };

/**
 * Toggle button that fetches and reveals the full text behind a selected
 * prompt variant — `prompt_variants` on a case is either the literal prompt
 * text or a `task.yaml`-style repo-relative path, so the name alone (what the
 * select shows) doesn't tell a person what the agent will actually read.
 */
export function PromptPreview({ entries, variant }: PromptPreviewProps) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>({ status: "idle" });

  // The variant (or the set of cases behind it) changed, so any cached
  // content is for the wrong prompt now — collapse and forget it.
  useEffect(() => {
    setOpen(false);
    setState({ status: "idle" });
  }, [variant, entries.map((entry) => entry.caseId).join(",")]);

  if (entries.length === 0) {
    return null;
  }

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (state.status === "loaded") {
      return;
    }
    setState({ status: "loading" });
    Promise.all(
      entries.map(async (entry) => ({
        label: entry.label,
        content: (await api.getCasePromptContent(entry.caseId, variant)).content
      }))
    )
      .then((items) => setState({ status: "loaded", items }))
      .catch((error: unknown) => {
        const message =
          error instanceof ApiError ? error.message : "Could not load this prompt.";
        setState({ status: "error", message });
      });
  };

  return (
    <div className="prompt-preview">
      <button
        aria-expanded={open}
        className="ghost prompt-preview-toggle"
        onClick={toggle}
        title={open ? "Hide the prompt text" : `View the "${variant}" prompt text`}
        type="button"
      >
        <IconSearch size={13} />
        {open ? "Hide prompt" : "View prompt"}
        <span className="prompt-preview-chevron">
          <IconChevronRight size={13} />
        </span>
      </button>
      {open ? (
        <div className="prompt-preview-body">
          {state.status === "loading" ? <p className="muted small">Loading…</p> : null}
          {state.status === "error" ? (
            <p className="muted small error">{state.message}</p>
          ) : null}
          {state.status === "loaded"
            ? state.items.map((item) => (
                <div key={item.label}>
                  {state.items.length > 1 ? (
                    <p className="muted small">{item.label}</p>
                  ) : null}
                  <pre className="command-preview prompt-preview-pre">{item.content}</pre>
                </div>
              ))
            : null}
        </div>
      ) : null}
    </div>
  );
}
