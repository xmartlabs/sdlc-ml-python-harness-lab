import { useEffect, useState } from "react";

import { api } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import { IconSettings } from "../components/icons";
import { defineMessages, useMessages } from "../i18n";
import type { StorageCategory, StoragePurgeScope, StorageReport } from "../types";

/**
 * Storage (ADR-0095): what is on disk, by category, and a button to purge
 * each scope. Purging is destructive and scoped — each button says exactly
 * what survives and what does not before it runs.
 */

const SCOPES: StoragePurgeScope[] = [
  "adhoc_expired",
  "adhoc_all_files",
  "benchmark_workspaces",
  "sandboxes"
];

const MESSAGES = defineMessages({
  en: {
    title: "Storage",
    lede: "What the lab keeps on disk, and a way to purge it.",
    total: "Total on disk",
    colCategory: "Category",
    colSize: "Size",
    colItems: "Items",
    purge: "Purge",
    purging: "Purging…",
    olderThanDays: "Older than (days)",
    freed: (bytes: string, runs: number) => `Freed ${bytes} across ${runs} runs.`,
    scopeLabels: {
      adhoc_expired: "Ad hoc tasks past retention",
      adhoc_all_files: "All ad hoc run files",
      benchmark_workspaces: "Benchmark workspaces",
      sandboxes: "Sandboxes"
    } as Record<StoragePurgeScope, string>,
    scopeConfirm: {
      adhoc_expired:
        "Deletes ad hoc run files older than their retention window. The metrics stay; applied runs with unexpired files are kept.",
      adhoc_all_files:
        "Deletes every ad hoc run's files right now, regardless of retention. The metrics stay; a run you have not decided on loses its diff.",
      benchmark_workspaces:
        "Deletes each scored benchmark run's workspace copy. Diff, logs, steps and metrics stay; sandbox, re-validation and continuation stop working for those runs.",
      sandboxes: "Stops and removes every sandbox container the lab still has running."
    } as Record<StoragePurgeScope, string>,
    cancel: "Cancel",
    confirmPurge: "Purge",
    errors: {
      load: "Could not read storage usage.",
      purge: "Could not purge that scope."
    }
  },
  es: {
    title: "Almacenamiento",
    lede: "Qué guarda el lab en disco, y una forma de purgarlo.",
    total: "Total en disco",
    colCategory: "Categoría",
    colSize: "Tamaño",
    colItems: "Elementos",
    purge: "Purgar",
    purging: "Purgando…",
    olderThanDays: "Más viejo que (días)",
    freed: (bytes: string, runs: number) => `Se liberaron ${bytes} en ${runs} corridas.`,
    scopeLabels: {
      adhoc_expired: "Tareas ad hoc vencidas",
      adhoc_all_files: "Todos los archivos de corridas ad hoc",
      benchmark_workspaces: "Workspaces de benchmarks",
      sandboxes: "Sandboxes"
    } as Record<StoragePurgeScope, string>,
    scopeConfirm: {
      adhoc_expired:
        "Borra archivos de corridas ad hoc más viejos que su ventana de retención. Las métricas quedan; las corridas aplicadas con archivos vigentes se conservan.",
      adhoc_all_files:
        "Borra los archivos de todas las corridas ad hoc ahora mismo, sin importar la retención. Las métricas quedan; una corrida que no decidiste pierde su diff.",
      benchmark_workspaces:
        "Borra la copia del workspace de cada corrida de benchmark puntuada. El diff, los logs, los pasos y las métricas quedan; el sandbox, la re-validación y la continuación dejan de funcionar para esas corridas.",
      sandboxes: "Para y elimina todos los contenedores sandbox que el lab todavía tiene corriendo."
    } as Record<StoragePurgeScope, string>,
    cancel: "Cancelar",
    confirmPurge: "Purgar",
    errors: {
      load: "No se pudo leer el uso de almacenamiento.",
      purge: "No se pudo purgar ese alcance."
    }
  }
});

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GiB`;
}

export function Storage() {
  const m = useMessages(MESSAGES);
  const [report, setReport] = useState<StorageReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyScope, setBusyScope] = useState<StoragePurgeScope | "">("");
  const [confirmScope, setConfirmScope] = useState<StoragePurgeScope | null>(null);
  const [olderThanDays, setOlderThanDays] = useState(14);
  const [lastFreed, setLastFreed] = useState<{ scope: StoragePurgeScope; bytes: number; runs: number } | null>(null);

  const load = () => {
    api
      .getStorage()
      .then(setReport)
      .catch(() => setError(m.errors.load));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const purge = async (scope: StoragePurgeScope) => {
    setBusyScope(scope);
    setError(null);
    try {
      const result = await api.purgeStorage(
        scope,
        scope === "benchmark_workspaces" ? olderThanDays : undefined
      );
      setLastFreed({ scope, bytes: result.freed_bytes, runs: result.runs });
      setConfirmScope(null);
      load();
    } catch (purgeError) {
      setError(purgeError instanceof Error ? purgeError.message : m.errors.purge);
    } finally {
      setBusyScope("");
    }
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow hub-eyebrow">
            <IconSettings size={14} />
            {m.title}
          </p>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {report === null ? (
        <Spinner />
      ) : (
        <>
          <div className="card">
            <p>
              <strong>{m.total}:</strong> {formatBytes(report.total_bytes)}
            </p>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{m.colCategory}</th>
                  <th>{m.colSize}</th>
                  <th>{m.colItems}</th>
                </tr>
              </thead>
              <tbody>
                {report.categories.map((category: StorageCategory) => (
                  <tr key={category.key}>
                    <td>{category.label}</td>
                    <td>{formatBytes(category.bytes)}</td>
                    <td>{category.items}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {lastFreed ? (
            <div className="callout callout--ok">
              <p>{m.freed(formatBytes(lastFreed.bytes), lastFreed.runs)}</p>
            </div>
          ) : null}

          <div className="card">
            {SCOPES.map((scope) => (
              <div className="row space-between" key={scope} style={{ marginBottom: "var(--space-3)" }}>
                <div>
                  <strong>{m.scopeLabels[scope]}</strong>
                  {scope === "benchmark_workspaces" ? (
                    <label className="field" style={{ marginTop: "var(--space-1)" }}>
                      <span>{m.olderThanDays}</span>
                      <input
                        min={0}
                        onChange={(event) => setOlderThanDays(Number(event.target.value))}
                        style={{ maxWidth: "120px" }}
                        type="number"
                        value={olderThanDays}
                      />
                    </label>
                  ) : null}
                </div>
                {confirmScope === scope ? (
                  <div className="row gap">
                    <span className="muted small">{m.scopeConfirm[scope]}</span>
                    <button
                      className="primary"
                      disabled={busyScope !== ""}
                      onClick={() => void purge(scope)}
                      type="button"
                    >
                      {busyScope === scope ? m.purging : m.confirmPurge}
                    </button>
                    <button className="ghost" onClick={() => setConfirmScope(null)} type="button">
                      {m.cancel}
                    </button>
                  </div>
                ) : (
                  <button className="ghost" onClick={() => setConfirmScope(scope)} type="button">
                    {m.purge}
                  </button>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
