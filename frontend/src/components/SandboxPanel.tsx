import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { SandboxRecord } from "../types";
import { ErrorBanner } from "./ErrorBanner";
import { Spinner } from "./Spinner";

type SandboxPanelProps = {
  runId: string;
  kind: "terminal" | "desktop";
};

const HINTS: Record<string, string> = {
  terminal:
    "A shell in a container with a copy of this run's workspace. Try: " +
    "`uv pip install -e . && pytest` (SWE-bench) or `python main.py` (headless).",
  desktop:
    "A Linux desktop in your browser (Xvfb + noVNC). Open the terminal on the " +
    "desktop and run `python main.py` to see the window (e.g. Tetris)."
};

export function SandboxPanel({ runId, kind }: SandboxPanelProps) {
  const [sandbox, setSandbox] = useState<SandboxRecord | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const clearPoll = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // Load any existing sandbox for this run+kind on mount / change.
  useEffect(() => {
    setSandbox(null);
    setError(null);
    let active = true;
    api
      .getSandbox(runId, kind)
      .then((result) => {
        if (active) {
          setSandbox(result);
          if (result.status === "starting") {
            startPolling();
          }
        }
      })
      .catch(() => {
        // 404 = no sandbox yet, expected.
      });
    return () => {
      active = false;
      clearPoll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, kind]);

  const startPolling = () => {
    clearPoll();
    let tries = 0;
    pollRef.current = window.setInterval(() => {
      tries += 1;
      api
        .getSandbox(runId, kind)
        .then((result) => {
          setSandbox(result);
          if (result.status !== "starting" || tries > 60) {
            clearPoll();
          }
        })
        .catch(() => {
          if (tries > 60) clearPoll();
        });
    }, 1500);
  };

  const handleLaunch = () => {
    setPending(true);
    setError(null);
    setSandbox({
      run_id: runId,
      kind,
      status: "starting",
      container_name: "",
      host_port: null,
      url: "",
      error: null
    });
    api
      .startSandbox(runId, kind)
      .then(() => startPolling())
      .catch((err: Error) => setError(err.message))
      .finally(() => setPending(false));
  };

  const handleStop = () => {
    setPending(true);
    setError(null);
    clearPoll();
    api
      .stopSandbox(runId, kind)
      .then(() => setSandbox((prev) => (prev ? { ...prev, status: "stopped", url: "" } : prev)))
      .catch((err: Error) => setError(err.message))
      .finally(() => setPending(false));
  };

  const status = sandbox?.status;
  const running = status === "running" && sandbox?.url;

  return (
    <section className="sandbox-panel">
      <div className="panel-head">
        <div>
          <h3>{kind === "terminal" ? "Terminal" : "Desktop"}</h3>
          <p className="muted">{HINTS[kind]}</p>
        </div>
        <div className="run-actions">
          {status ? <span className={`badge badge--${status === "running" ? "completed" : status === "failed" ? "failed" : "running"}`}>{status}</span> : null}
          {running ? (
            <a className="button-link" href={sandbox!.url} rel="noreferrer" target="_blank">
              Open in tab
            </a>
          ) : null}
          {running ? (
            <button disabled={pending} onClick={handleStop} type="button">
              Stop
            </button>
          ) : (
            <button className="primary" disabled={pending || status === "starting"} onClick={handleLaunch} type="button">
              {status === "starting" ? "Starting…" : "Launch"}
            </button>
          )}
        </div>
      </div>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {sandbox?.status === "failed" && sandbox.error ? (
        <ErrorBanner message={sandbox.error} />
      ) : null}

      {status === "starting" ? (
        <div className="sandbox-empty">
          <Spinner />
          <p className="muted">Starting the {kind} sandbox (copying workspace, launching container)…</p>
        </div>
      ) : running ? (
        <iframe className="sandbox-frame" src={sandbox!.url} title={`${kind} sandbox`} />
      ) : (
        <div className="sandbox-empty">
          <p className="muted">
            No {kind} running. Click Launch to open a sandbox with a copy of this run's
            workspace.
          </p>
        </div>
      )}
    </section>
  );
}
