import { useEffect, useState } from "react";
import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { ArtifactRecord } from "../types";
import { Markdown } from "./Markdown";

type ArtifactsPanelProps = {
  runId: string;
};

const VIEWABLE_MEDIA_PREFIXES = ["text/", "application/json"];
const MAX_LISTED = 200;

const MESSAGES = defineMessages({
  en: {
    indexing: "Indexing...",
    indexArtifacts: "Index artifacts",
    refresh: "Refresh",
    notIndexedHint: "Workspace files are not indexed — use the Terminal tab to browse them",
    noArtifactsYet:
      "No artifacts indexed yet. Expected files: metadata.json, prompt.md, report.md, " +
      "audit.md, diff.patch."
  },
  es: {
    indexing: "Indexando...",
    indexArtifacts: "Indexar artefactos",
    refresh: "Actualizar",
    notIndexedHint:
      "Los archivos del workspace no están indexados — usá la pestaña Terminal para navegarlos",
    noArtifactsYet:
      "Todavía no hay artefactos indexados. Archivos esperados: metadata.json, prompt.md, " +
      "report.md, audit.md, diff.patch."
  }
});

function isViewable(artifact: ArtifactRecord): boolean {
  return (
    VIEWABLE_MEDIA_PREFIXES.some((prefix) => artifact.media_type.startsWith(prefix)) ||
    /\.(md|json|log|patch|diff|txt|yaml|yml|toml|py|env)$/.test(artifact.name)
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArtifactsPanel({ runId }: ArtifactsPanelProps) {
  const m = useMessages(MESSAGES);
  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setArtifacts([]);
    setSelected(null);
    setContent(null);
    setError(null);
    let active = true;
    api
      .listArtifacts(runId)
      .then((items) => {
        if (active) {
          setArtifacts(items);
        }
      })
      .catch(() => {
        // No index yet — the Refresh button triggers indexing on demand.
      });
    return () => {
      active = false;
    };
  }, [runId]);

  const handleRefresh = () => {
    setLoading(true);
    setError(null);
    api
      .indexArtifacts(runId)
      .then((items) => setArtifacts(items))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  };

  const handleOpen = (artifact: ArtifactRecord) => {
    if (selected === artifact.name) {
      setSelected(null);
      setContent(null);
      return;
    }
    setSelected(artifact.name);
    setContent(null);
    setError(null);
    api
      .readArtifact(runId, artifact.name)
      .then((result) => setContent(result.content))
      .catch((err: Error) => setError(err.message));
  };

  const selectedArtifact = artifacts.find((item) => item.name === selected) ?? null;
  // The server stopped indexing workspace/ (a cloned repo is tens of thousands
  // of files nothing here lists), but runs indexed before that still carry
  // those rows, so the filter stays until they are re-indexed.
  const topLevel = artifacts.filter((item) => !item.name.startsWith("workspace/"));
  const listed = topLevel.slice(0, MAX_LISTED);

  return (
    <div>
      <div className="panel-actions">
        <button disabled={loading} onClick={handleRefresh} type="button">
          {loading ? m.indexing : artifacts.length === 0 ? m.indexArtifacts : m.refresh}
        </button>
        <span className="muted">{m.notIndexedHint}</span>
      </div>
      {error ? <p className="muted">{error}</p> : null}
      {listed.length === 0 ? (
        <p className="muted">{m.noArtifactsYet}</p>
      ) : (
        <div className="table">
          {listed.map((artifact) => (
            <button
              className={selected === artifact.name ? "table-row selectable active" : "table-row selectable"}
              disabled={!isViewable(artifact)}
              key={artifact.name}
              onClick={() => handleOpen(artifact)}
              type="button"
            >
              <span>
                <strong>{artifact.name}</strong>
              </span>
              <span className="muted">{artifact.media_type || "—"}</span>
              <span className="muted">{formatSize(artifact.size_bytes)}</span>
            </button>
          ))}
        </div>
      )}
      {selectedArtifact !== null && content !== null ? (
        <div className="artifact-viewer">
          <p className="sidebar-label">{selectedArtifact.name}</p>
          {selectedArtifact.name.endsWith(".md") ? (
            <Markdown>{content}</Markdown>
          ) : (
            <pre className="artifact-pre">{content}</pre>
          )}
        </div>
      ) : null}
    </div>
  );
}
