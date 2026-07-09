import { useEffect, useState } from "react";
import { api } from "../api";
import type { ArtifactRecord } from "../types";
import { Markdown } from "./Markdown";

type ArtifactsPanelProps = {
  runId: string;
};

const VIEWABLE_MEDIA_PREFIXES = ["text/", "application/json"];
const MAX_LISTED = 200;

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
  // Workspace files can be numerous; keep the list focused on run artifacts.
  const topLevel = artifacts.filter((item) => !item.name.startsWith("workspace/"));
  const workspaceCount = artifacts.length - topLevel.length;
  const listed = topLevel.slice(0, MAX_LISTED);

  return (
    <div>
      <div className="panel-actions">
        <button disabled={loading} onClick={handleRefresh} type="button">
          {loading ? "Indexing..." : artifacts.length === 0 ? "Index artifacts" : "Refresh"}
        </button>
        {workspaceCount > 0 ? (
          <span className="muted">+ {workspaceCount} workspace files (use the Terminal tab to browse)</span>
        ) : null}
      </div>
      {error ? <p className="muted">{error}</p> : null}
      {listed.length === 0 ? (
        <p className="muted">
          No artifacts indexed yet. Expected files: metadata.json, prompt.md, report.md,
          audit.md, diff.patch.
        </p>
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
