import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { SandboxRecord } from "../types";
import { ErrorBanner } from "./ErrorBanner";
import { Spinner } from "./Spinner";

type SandboxPanelProps = {
  runId: string;
  kind: "terminal" | "desktop" | "play";
};

const MESSAGES = defineMessages({
  en: {
    kindLabel: { terminal: "Terminal", desktop: "Desktop", play: "Play" },
    hints: {
      terminal:
        "A shell in a container with a copy of this run's workspace. Try: " +
        "`uv pip install -e . && pytest` (SWE-bench) or `python main.py` (headless).",
      desktop:
        "A Linux desktop in your browser (Xvfb + noVNC). Open the terminal on the " +
        "desktop and run `python main.py` to see the window (e.g. Tetris).",
      play:
        "The game this run built, started for you on a desktop in your browser. " +
        "It renders on the CPU, so heavy scenes run slower than on a real GPU. " +
        "If it closes, a window shows why; the terminal underneath can relaunch it."
    },
    openInTab: "Open in tab",
    stop: "Stop",
    starting: "Starting…",
    launch: "Launch",
    sandboxTitle: (label: string) => `${label} sandbox`,
    startingSandbox: (label: string) =>
      `Starting the ${label} sandbox (copying workspace, launching container)…`,
    noneRunning: (label: string) =>
      `No ${label} running. Click Launch to open a sandbox with a copy of this run's workspace.`
  },
  es: {
    kindLabel: { terminal: "Terminal", desktop: "Escritorio", play: "Jugar" },
    hints: {
      terminal:
        "Una terminal en un contenedor con una copia del workspace de esta corrida. " +
        "Probá: `uv pip install -e . && pytest` (SWE-bench) o `python main.py` (sin interfaz).",
      desktop:
        "Un escritorio Linux en tu navegador (Xvfb + noVNC). Abrí la terminal del " +
        "escritorio y corré `python main.py` para ver la ventana (por ejemplo, Tetris).",
      play:
        "El juego que construyó esta corrida, abierto en un escritorio en tu navegador. " +
        "Se renderiza en la CPU, así que las escenas pesadas van más lentas que con una " +
        "GPU real. Si se cierra, una ventana muestra por qué; la terminal de abajo lo relanza."
    },
    openInTab: "Abrir en una pestaña",
    stop: "Detener",
    starting: "Iniciando…",
    launch: "Lanzar",
    sandboxTitle: (label: string) => `Sandbox de ${label.toLowerCase()}`,
    startingSandbox: (label: string) =>
      `Iniciando el sandbox de ${label.toLowerCase()} (copiando el workspace, lanzando el contenedor)…`,
    noneRunning: (label: string) =>
      `No hay ${label.toLowerCase()} corriendo. Hacé clic en Lanzar para abrir un sandbox ` +
      "con una copia del workspace de esta corrida."
  }
});

export function SandboxPanel({ runId, kind }: SandboxPanelProps) {
  const m = useMessages(MESSAGES);
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

  const kindLabel = m.kindLabel[kind];

  return (
    <section className="sandbox-panel">
      <div className="panel-head">
        <div>
          <h3>{kindLabel}</h3>
          <p className="muted">{m.hints[kind]}</p>
        </div>
        <div className="run-actions">
          {status ? <span className={`badge badge--${status === "running" ? "completed" : status === "failed" ? "failed" : "running"}`}>{status}</span> : null}
          {running ? (
            <a className="button-link" href={sandbox!.url} rel="noreferrer" target="_blank">
              {m.openInTab}
            </a>
          ) : null}
          {running ? (
            <button disabled={pending} onClick={handleStop} type="button">
              {m.stop}
            </button>
          ) : (
            <button className="primary" disabled={pending || status === "starting"} onClick={handleLaunch} type="button">
              {status === "starting" ? m.starting : m.launch}
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
          <p className="muted">{m.startingSandbox(kindLabel)}</p>
        </div>
      ) : running ? (
        <iframe className="sandbox-frame" src={sandbox!.url} title={m.sandboxTitle(kindLabel)} />
      ) : (
        <div className="sandbox-empty">
          <p className="muted">{m.noneRunning(kindLabel)}</p>
        </div>
      )}
    </section>
  );
}
