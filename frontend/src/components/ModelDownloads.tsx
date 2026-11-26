import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { defineMessages, useMessages } from "../i18n";
import { createLatest, nextPollStep, pollDelay } from "../local/pullPolling.mjs";
import type {
  LocalGuardReport,
  LocalHubModel,
  LocalHubQuant,
  LocalPullBusyDetail,
  LocalPullJob,
  LocalServerKind,
  MachineSpec
} from "../types";

/**
 * *Get more models* on the Your server step (ADR-0101): find a model, see
 * whether it fits the machine from step 1, and ask the same server the page
 * talks to — this computer or another one on the network — to download it.
 */

type Tab = "recommended" | "search" | "exact";

const POLL_MS = 1500;
/** Above this, the download asks for one more click: it fills a small box's disk. */
const LARGE_DOWNLOAD_BYTES = 10e9;

const MESSAGES = defineMessages({
  en: {
    heading: "Get more models",
    intro: (host: string) =>
      `Downloads go to the server above (${host}), the same machine that will run the model. Fit is checked against your machine from step 1.`,
    tabs: {
      recommended: "Recommended",
      search: "Search Hugging Face",
      exact: "Exact name"
    } as Record<Tab, string>,
    searchPlaceholder: "qwen3 coder, gemma, llama 3.1…",
    search: "Search",
    searching: "Searching…",
    exactPlaceholder: { ollama: "qwen3:14b", lmstudio: "qwen/qwen3-14b" } as Record<LocalServerKind, string>,
    exactHint: {
      ollama: "A name from ollama.com/library (qwen3:14b) or a Hugging Face file (hf.co/owner/repo:Q4_K_M).",
      lmstudio: "An LM Studio catalog name (qwen/qwen3-14b) or a Hugging Face link (https://huggingface.co/owner/repo)."
    } as Record<LocalServerKind, string>,
    noResults: "No GGUF models match. Try a shorter name, like the model family.",
    download: "Download",
    downloaded: "Downloaded",
    onServer: "On the server",
    confirmLarge: (gb: string) => `Download ${gb} GB?`,
    tools: "Tool use",
    noTools: "No tool use",
    downloads: (n: string) => `${n} downloads`,
    fit: {
      ok: "Fits at 32k",
      tight: "Tight at 32k",
      upTo: (ctx: string) => `Fits up to ${ctx}`,
      no: "Too big for this machine",
      unknown: "Size unknown"
    },
    tooBigNote: "The memory estimate says it will not load here. You can still download it, for another machine or to try anyway.",
    progress: (done: string, total: string) => `${done} of ${total} GB`,
    starting: "Starting…",
    doneNote: (model: string) => `${model} is on the server. It is now in the list above — pick it in step 3.`,
    failed: "Download failed",
    busyRuns: (n: number) =>
      `${n} lab run${n === 1 ? "" : "s"} use this host. The download shares its disk and network, so they may slow down.`,
    lostTrack:
      "The lab lost track of this download (it restarted or stopped answering). The server keeps downloading; test the connection again in a while to see the model.",
    runningElsewhere: (model: string) => `Downloading ${model} on this server`,
    orCommand: "Or run it on that machine yourself:",
    copy: "Copy",
    copied: "Copied"
  },
  es: {
    heading: "Descargar más modelos",
    intro: (host: string) =>
      `Las descargas van al servidor de arriba (${host}), la misma máquina que va a correr el modelo. Si entra se calcula con tu máquina del paso 1.`,
    tabs: {
      recommended: "Recomendados",
      search: "Buscar en Hugging Face",
      exact: "Nombre exacto"
    } as Record<Tab, string>,
    searchPlaceholder: "qwen3 coder, gemma, llama 3.1…",
    search: "Buscar",
    searching: "Buscando…",
    exactPlaceholder: { ollama: "qwen3:14b", lmstudio: "qwen/qwen3-14b" } as Record<LocalServerKind, string>,
    exactHint: {
      ollama: "Un nombre de ollama.com/library (qwen3:14b) o un archivo de Hugging Face (hf.co/owner/repo:Q4_K_M).",
      lmstudio: "Un nombre del catálogo de LM Studio (qwen/qwen3-14b) o un link de Hugging Face (https://huggingface.co/owner/repo)."
    } as Record<LocalServerKind, string>,
    noResults: "Ningún modelo GGUF coincide. Probá con un nombre más corto, como la familia del modelo.",
    download: "Descargar",
    downloaded: "Descargado",
    onServer: "Ya está en el servidor",
    confirmLarge: (gb: string) => `¿Descargar ${gb} GB?`,
    tools: "Usa herramientas",
    noTools: "Sin herramientas",
    downloads: (n: string) => `${n} descargas`,
    fit: {
      ok: "Entra a 32k",
      tight: "Justo a 32k",
      upTo: (ctx: string) => `Entra hasta ${ctx}`,
      no: "No entra en esta máquina",
      unknown: "Tamaño desconocido"
    },
    tooBigNote: "La estimación de memoria dice que no va a cargar acá. Igual podés descargarlo, para otra máquina o para probar.",
    progress: (done: string, total: string) => `${done} de ${total} GB`,
    starting: "Arrancando…",
    doneNote: (model: string) => `${model} ya está en el servidor. Aparece en la lista de arriba: elegilo en el paso 3.`,
    failed: "La descarga falló",
    busyRuns: (n: number) =>
      `${n} corrida${n === 1 ? "" : "s"} del lab usa${n === 1 ? "" : "n"} este host. La descarga comparte disco y red, así que pueden ir más lentas.`,
    lostTrack:
      "El lab perdió el seguimiento de esta descarga (se reinició o dejó de responder). El servidor sigue descargando; probá la conexión de nuevo en un rato para ver el modelo.",
    runningElsewhere: (model: string) => `Descargando ${model} en este servidor`,
    orCommand: "O corrélo vos en esa máquina:",
    copy: "Copiar",
    copied: "Copiado"
  }
});

function gb(bytes: number | null | undefined): string {
  return bytes ? (bytes / 1e9).toFixed(1) : "?";
}

function contextLabel(tokens: number): string {
  return tokens >= 1024 ? `${Math.round(tokens / 1024)}k` : String(tokens);
}

/** What the server is asked to pull for this file, and the matching shell command. */
function pullTarget(kind: LocalServerKind, quant: LocalHubQuant): { model: string; quantization: string; command: string } {
  if (kind === "ollama") {
    return { model: quant.ollama_model, quantization: "", command: `ollama pull ${quant.ollama_model}` };
  }
  return {
    model: quant.lmstudio_model,
    quantization: quant.name,
    command: `lms get ${quant.lmstudio_model} --gguf`
  };
}

function fitBadge(quant: LocalHubQuant, m: (typeof MESSAGES)["en"]): { className: string; label: string } {
  if (quant.verdict === null) return { className: "badge neutral", label: m.fit.unknown };
  if (quant.verdict === "ok") return { className: "badge ok", label: m.fit.ok };
  if (quant.verdict === "tight") return { className: "badge warn", label: m.fit.tight };
  if (quant.fit_context) return { className: "badge warn", label: m.fit.upTo(contextLabel(quant.fit_context)) };
  return { className: "badge error", label: m.fit.no };
}

/** True when a hub file is already one of the server's downloaded models. */
function alreadyOnServer(kind: LocalServerKind, quant: LocalHubQuant, repo: string, downloadedIds: string[]): boolean {
  const ids = downloadedIds.map((id) => id.toLowerCase());
  if (kind === "ollama") {
    const wanted = quant.ollama_model.toLowerCase();
    return ids.some((id) => id === wanted || id === `${wanted}:latest`);
  }
  // LM Studio keys a Hugging Face download by its repo's model name.
  const name = repo.split("/").pop()?.toLowerCase().replace(/-gguf$/, "") ?? "";
  return name !== "" && ids.some((id) => id.split("/").pop() === name);
}

type ModelDownloadsProps = {
  kind: LocalServerKind;
  url: string;
  machine: MachineSpec | null;
  downloadedIds: string[];
  /** Called once a download completes, so the page can list the server's models again. */
  onDownloaded: () => void;
};

export function ModelDownloads({ kind, url, machine, downloadedIds, onDownloaded }: ModelDownloadsProps) {
  const m = useMessages(MESSAGES);
  const [tab, setTab] = useState<Tab>("recommended");
  const [models, setModels] = useState<LocalHubModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searched, setSearched] = useState(false);
  const [exact, setExact] = useState("");
  const [picked, setPicked] = useState<Record<string, number>>({});
  const [confirming, setConfirming] = useState<string | null>(null);
  const [job, setJob] = useState<LocalPullJob | null>(null);
  const [jobKey, setJobKey] = useState<string | null>(null);
  const [guard, setGuard] = useState<LocalGuardReport | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  // False once the panel unmounts (a re-test of the server, another step):
  // an answer still in flight must not schedule a poll or touch the page.
  const aliveRef = useRef(true);
  const searchTickets = useRef(createLatest());
  const onDownloadedRef = useRef(onDownloaded);
  onDownloadedRef.current = onDownloaded;

  const machineKey = machine ? JSON.stringify(machine) : "";

  const runSearch = (text: string) => {
    const ticket = searchTickets.current.next();
    setLoading(true);
    setError(null);
    api.local
      .hubSearch(text, machine)
      .then((result) => {
        if (!aliveRef.current || !searchTickets.current.isLatest(ticket)) return;
        setModels(result.models);
        setError(result.error || null);
        setSearched(text !== "");
      })
      .catch((err: Error) => {
        if (aliveRef.current && searchTickets.current.isLatest(ticket)) setError(err.message);
      })
      .finally(() => {
        if (aliveRef.current && searchTickets.current.isLatest(ticket)) setLoading(false);
      });
  };

  // The recommended list depends on the machine from step 1 for its fit badges.
  useEffect(() => {
    if (tab === "recommended") runSearch("");
  }, [tab, machineKey]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      searchTickets.current.cancel();
      if (pollRef.current !== null) window.clearTimeout(pollRef.current);
      pollRef.current = null;
    };
  }, []);

  const schedulePoll = (jobId: string, failures: number) => {
    if (!aliveRef.current) return;
    pollRef.current = window.setTimeout(() => poll(jobId, failures), pollDelay(failures, POLL_MS));
  };

  const poll = (jobId: string, failures = 0) => {
    api.local
      .getPull(jobId)
      .then((next) => {
        if (!aliveRef.current) return;
        setJob(next);
        const step = nextPollStep({ job: next }, failures);
        if (step.action === "continue") schedulePoll(jobId, 0);
        else if (step.action === "done") onDownloadedRef.current();
      })
      .catch((err: Error) => {
        if (!aliveRef.current) return;
        const status = err instanceof ApiError ? err.status : 0;
        const step = nextPollStep({ status }, failures);
        if (step.action === "retry") {
          schedulePoll(jobId, step.failures);
          return;
        }
        // "forget" (the API restarted or pruned the job) or "give_up": the
        // server keeps downloading on its own and the model shows up in the
        // next listing, so release the page instead of leaving it disabled.
        setJob(null);
        setJobKey(null);
        setError(m.lostTrack);
      });
  };

  const follow = (key: string, started: LocalPullJob) => {
    setJobKey(key);
    setJob(started);
    poll(started.id);
  };

  const startDownload = (key: string, model: string, quantization: string) => {
    setConfirming(null);
    setError(null);
    setGuard(null);
    setJobKey(key);
    setJob(null);
    api.local
      .startPull(kind, url, model, quantization)
      .then((started) => {
        if (!aliveRef.current) return;
        if (!started.guard.clear) setGuard(started.guard);
        follow(key, started.job);
      })
      .catch((err: Error) => {
        if (!aliveRef.current) return;
        if (err instanceof ApiError && err.status === 409) {
          const detail = err.detail as LocalPullBusyDetail | undefined;
          if (detail?.job) {
            // Another tab (or a remount) started it: follow that download.
            setError(detail.message);
            follow(`running:${detail.job.model}`, detail.job);
            return;
          }
        }
        setJobKey(null);
        setError(err.message);
      });
  };

  const copy = (command: string) => {
    navigator.clipboard
      .writeText(command)
      .then(() => setCopied(command))
      .catch(() => setCopied(null));
  };

  const downloading = job?.status === "downloading";
  const host = (() => {
    try {
      return new URL(url).host;
    } catch {
      return url;
    }
  })();

  const renderProgress = (key: string) => {
    if (jobKey !== key) return null;
    if (!job) return <p className="muted small model-download-progress">{m.starting}</p>;
    if (job.status === "failed") {
      return (
        <p className="model-download-progress model-download-error">
          <strong>{m.failed}:</strong> {job.error}
        </p>
      );
    }
    if (job.status === "completed") {
      return <p className="callout callout--ok model-download-progress">{m.doneNote(job.model)}</p>;
    }
    const pct = job.total_bytes && job.completed_bytes ? Math.min(100, (job.completed_bytes / job.total_bytes) * 100) : 0;
    return (
      <div className="model-download-progress">
        <div
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={Math.round(pct)}
          className="model-download-track"
          role="progressbar"
        >
          <span style={{ width: `${pct}%` }} />
        </div>
        <span className="muted small">
          {job.total_bytes ? m.progress(gb(job.completed_bytes), gb(job.total_bytes)) : m.starting}
          {job.detail && job.detail !== "downloading" ? ` · ${job.detail}` : ""}
        </span>
      </div>
    );
  };

  const renderModel = (model: LocalHubModel) => {
    const index = Math.min(picked[model.repo] ?? defaultQuant(model), model.quants.length - 1);
    const quant = model.quants[index];
    const key = `${model.repo}:${quant.name}`;
    const target = pullTarget(kind, quant);
    const badge = fitBadge(quant, m);
    const onServer = alreadyOnServer(kind, quant, model.repo, downloadedIds);
    const thisDone = jobKey === key && job?.status === "completed";
    const large = (quant.size_bytes ?? 0) > LARGE_DOWNLOAD_BYTES;
    return (
      <li className="model-download" key={model.repo}>
        <div className="model-download-main">
          <div className="model-download-name">
            <strong>{model.label || model.repo}</strong>
            <span className="muted small">
              {[
                model.params,
                model.supports_tools === true ? m.tools : model.supports_tools === false ? m.noTools : "",
                model.downloads ? m.downloads(model.downloads.toLocaleString()) : ""
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
          <select
            aria-label={`${model.label || model.repo} quantization`}
            disabled={downloading || model.quants.length < 2}
            id={`quant-${model.repo}`}
            onChange={(event) => {
              setPicked({ ...picked, [model.repo]: Number(event.target.value) });
              setConfirming(null);
            }}
            value={index}
          >
            {model.quants.map((q, i) => (
              <option key={q.name} value={i}>
                {q.name} · {gb(q.size_bytes)} GB
              </option>
            ))}
          </select>
          <span className={badge.className}>{badge.label}</span>
          {onServer || thisDone ? (
            <span className="badge ok">{thisDone ? m.downloaded : m.onServer}</span>
          ) : confirming === key ? (
            <button className="primary" onClick={() => startDownload(key, target.model, target.quantization)} type="button">
              {m.confirmLarge(gb(quant.size_bytes))}
            </button>
          ) : (
            <button
              className="secondary"
              disabled={downloading}
              onClick={() => (large ? setConfirming(key) : startDownload(key, target.model, target.quantization))}
              type="button"
            >
              {m.download}
            </button>
          )}
        </div>
        {badge.className === "badge error" && confirming === key ? <p className="muted small">{m.tooBigNote}</p> : null}
        {renderProgress(key)}
        {jobKey === key && job?.status === "failed" ? (
          <div className="local-setup-command">
            <span className="muted small">{m.orCommand}</span>
            <pre className="command-preview">{target.command}</pre>
            <button className="secondary" onClick={() => copy(target.command)} type="button">
              {copied === target.command ? m.copied : m.copy}
            </button>
          </div>
        ) : null}
      </li>
    );
  };

  const exactKey = jobKey?.startsWith("exact:") ? jobKey : `exact:${exact.trim()}`;
  const runningKey = jobKey?.startsWith("running:") ? jobKey : null;

  return (
    <div className="panel model-downloads">
      <div className="panel-head">
        <h3>{m.heading}</h3>
      </div>
      <p className="muted small">{m.intro(host)}</p>
      <div className="segmented" role="tablist">
        {(["recommended", "search", "exact"] as Tab[]).map((value) => (
          <button
            aria-selected={tab === value}
            className={tab === value ? "active" : ""}
            key={value}
            onClick={() => {
              setTab(value);
              setError(null);
              if (value !== "recommended") setModels([]);
              setSearched(false);
            }}
            role="tab"
            type="button"
          >
            {m.tabs[value]}
          </button>
        ))}
      </div>

      {tab === "search" ? (
        <form
          className="model-download-search"
          onSubmit={(event) => {
            event.preventDefault();
            if (query.trim()) runSearch(query.trim());
          }}
        >
          <input
            aria-label={m.tabs.search}
            id="model-download-query"
            onChange={(event) => setQuery(event.target.value)}
            placeholder={m.searchPlaceholder}
            type="search"
            value={query}
          />
          <button className="secondary" disabled={loading || !query.trim()} type="submit">
            {loading ? m.searching : m.search}
          </button>
        </form>
      ) : null}

      {tab === "exact" ? (
        <div className="model-download-exact">
          <form
            className="model-download-search"
            onSubmit={(event) => {
              event.preventDefault();
              if (exact.trim()) startDownload(exactKey, exact.trim(), "");
            }}
          >
            <input
              aria-label={m.tabs.exact}
              id="model-download-exact"
              disabled={downloading}
              onChange={(event) => setExact(event.target.value)}
              placeholder={m.exactPlaceholder[kind]}
              value={exact}
            />
            <button className="primary" disabled={downloading || !exact.trim()} type="submit">
              {m.download}
            </button>
          </form>
          <p className="muted small">{m.exactHint[kind]}</p>
          {renderProgress(exactKey)}
        </div>
      ) : null}

      {runningKey && job ? (
        <div className="model-download">
          <strong>{m.runningElsewhere(job.model)}</strong>
          {renderProgress(runningKey)}
        </div>
      ) : null}

      {guard && !guard.clear && downloading ? (
        <p className="callout callout--warning">{m.busyRuns(guard.running.length + guard.queued.length)}</p>
      ) : null}
      {error ? <p className="callout callout--danger">{error}</p> : null}

      {tab !== "exact" ? (
        loading && models.length === 0 ? (
          <p className="muted small">{m.searching}</p>
        ) : models.length > 0 ? (
          <ul className="model-download-list">{models.map(renderModel)}</ul>
        ) : searched && !error ? (
          <p className="muted small">{m.noResults}</p>
        ) : null
      ) : null}
    </div>
  );
}

/**
 * The quantization a row opens on: Q4_K_M when it fits (the usual choice), else
 * the largest file that fits, else Q4_K_M, else the smallest.
 */
function defaultQuant(model: LocalHubModel): number {
  const fits = (q: LocalHubQuant) => q.verdict === "ok" || q.verdict === "tight";
  const q4 = model.quants.findIndex((q) => q.name.toUpperCase() === "Q4_K_M");
  if (q4 !== -1 && fits(model.quants[q4])) return q4;
  let best = -1;
  model.quants.forEach((q, i) => {
    if (fits(q) && (best === -1 || (q.size_bytes ?? 0) > (model.quants[best].size_bytes ?? 0))) best = i;
  });
  if (best !== -1) return best;
  return q4 !== -1 ? q4 : 0;
}
