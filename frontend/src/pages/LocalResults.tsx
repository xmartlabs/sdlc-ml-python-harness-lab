import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState } from "../components/EmptyState";
import { HelpPanel } from "../components/HelpPanel";
import { api } from "../api";
import { formatRunDate } from "../dates";
import { defineMessages, useMessages } from "../i18n";
import type {
  LocalHeadroomLevel,
  LocalReferenceDevice,
  LocalReferenceDeviceKind,
  LocalReferenceDevices,
  LocalReferenceModel,
  LocalResults as LocalResultsData,
  LocalVerdict
} from "../types";

const MESSAGES = defineMessages({
  en: {
    header: {
      title: "Results",
      lede: "What has already been measured on local models: speed tests, and real cases run with one as the executor."
    },
    help: {
      title: "How to read this page",
      body: "Speed tests come from \"Tune your local model\" and say how fast a configuration serves. Case runs come from real evaluations and say whether the model does the work — they never share one score, because a case run also depends on the harness and the prompt."
    },
    speed: {
      heading: "Speed tests",
      note: "Speed depends on the machine; compare rows of the same machine.",
      allMachines: "All machines",
      colRank: "#",
      colMachine: "Machine",
      colModel: "Model",
      colConfig: "Configuration",
      colSecondsPerStep: "s/step",
      colReadSpeed: "Read tok/s",
      colWriteSpeed: "Write tok/s",
      colAgent: "Agent passed/total",
      colContext: "Context",
      colFit: "Fit",
      colWindow: "Window",
      colDate: "Date",
      colVsCeiling: "vs ceiling",
      recommended: "Recommended",
      empty: "No speed tests saved yet.",
      emptyAction: "Tune a local model"
    },
    reference: {
      heading: "Published reference",
      selectModel: "Model",
      selectHardware: "Hardware",
      allModels: "All models",
      allHardware: "All hardware",
      groupGpu: "GPU",
      groupUnified: "Unified memory",
      groupCpu: "CPU",
      colModel: "Model",
      colHardware: "Hardware",
      colWrite: "Write tok/s",
      colRead: "Read tok/s",
      colCeiling: "Ceiling (tok/s)",
      colPctCeiling: "% of ceiling",
      colRuntime: "Runtime",
      colNote: "Note",
      colSource: "Source",
      sourceLink: "source",
      secondaryBadge: "secondary",
      secondaryTitle: "Read from a summary table or an aggregator, not a raw bench row.",
      computeBoundTitle: "compute-bound quant",
      you: "You",
      vos: "Vos",
      computedNote:
        "No published figure: bandwidth ceiling only; well-tuned setups reach 60–80% of it.",
      deviceSpec: (label: string, memory: string, bandwidth: string) =>
        `${label} · ${memory} · ${bandwidth} GB/s`,
      note: "Published single-user speeds for this model. Rows differ in context depth/runtime (see Note); compare against rows from the same runtime family. Only figures read on the cited page are listed — a missing device means nothing citable was found. \"secondary\" = read from a summary table or aggregator."
    },
    headroomLevel: {
      near_ceiling: "Near the ceiling",
      some_headroom: "Some headroom",
      far_below: "Far below",
      compute_bound: "Compute-bound quant"
    } as Record<LocalHeadroomLevel, string>,
    cases: {
      heading: "Case runs with local models",
      note: "Token speed is not recorded for local case runs; see Speed tests for each model's tok/s.",
      armNote: "Each arm is its own row: arms are never pooled.",
      colRank: "#",
      colModel: "Model",
      colHarness: "Harness",
      colCase: "Case",
      colArm: "Arm",
      colRuns: "Runs",
      colPassed: "Passed",
      colPassRate: "Graded tests passed",
      colMedianMinutes: "Median minutes",
      empty: "No case runs with local models yet.",
      emptyAction: "Tune a local model"
    }
  },
  es: {
    header: {
      title: "Resultados",
      lede: "Lo que ya se midió con modelos locales: pruebas de velocidad, y casos reales corridos con uno de ejecutor."
    },
    help: {
      title: "Cómo leer esta página",
      body: "Las pruebas de velocidad vienen de \"Optimizá tu modelo local\" y dicen qué tan rápido sirve una configuración. Las corridas de casos vienen de evaluaciones reales y dicen si el modelo hace el trabajo — nunca comparten un puntaje, porque una corrida de caso también depende del harness y el prompt."
    },
    speed: {
      heading: "Pruebas de velocidad",
      note: "La velocidad depende de la máquina; comparar filas de la misma máquina.",
      allMachines: "Todas las máquinas",
      colRank: "#",
      colMachine: "Máquina",
      colModel: "Modelo",
      colConfig: "Configuración",
      colSecondsPerStep: "s/paso",
      colReadSpeed: "Tok/s lectura",
      colWriteSpeed: "Tok/s escritura",
      colAgent: "Agente pasados/total",
      colContext: "Contexto",
      colFit: "Entra",
      colWindow: "Ventana",
      colDate: "Fecha",
      colVsCeiling: "vs techo",
      recommended: "Recomendado",
      empty: "Todavía no hay pruebas de velocidad guardadas.",
      emptyAction: "Optimizá un modelo local"
    },
    reference: {
      heading: "Referencia publicada",
      selectModel: "Modelo",
      selectHardware: "Hardware",
      allModels: "Todos los modelos",
      allHardware: "Todo el hardware",
      groupGpu: "GPU",
      groupUnified: "Memoria unificada",
      groupCpu: "CPU",
      colModel: "Modelo",
      colHardware: "Hardware",
      colWrite: "Tok/s escritura",
      colRead: "Tok/s lectura",
      colCeiling: "Techo (tok/s)",
      colPctCeiling: "% del techo",
      colRuntime: "Runtime",
      colNote: "Nota",
      colSource: "Fuente",
      sourceLink: "fuente",
      secondaryBadge: "secundaria",
      secondaryTitle: "Leído de una tabla resumen o un agregador, no de una fila de benchmark cruda.",
      computeBoundTitle: "cuantización limitada por cómputo",
      you: "Vos",
      vos: "Vos",
      computedNote:
        "Sin cifra publicada: solo techo de ancho de banda; una configuración bien afinada llega al 60-80%.",
      deviceSpec: (label: string, memory: string, bandwidth: string) =>
        `${label} · ${memory} · ${bandwidth} GB/s`,
      note: "Velocidades de un solo usuario publicadas para este modelo. Las filas difieren en profundidad de contexto/runtime (ver Nota); comparar contra filas de la misma familia de runtime. Solo se listan cifras leídas en la página citada — un dispositivo faltante significa que no se encontró nada citable. \"secundaria\" = leído de una tabla resumen o un agregador."
    },
    headroomLevel: {
      near_ceiling: "Cerca del techo",
      some_headroom: "Algo de margen",
      far_below: "Muy por debajo",
      compute_bound: "Cuantización limitada por cómputo"
    } as Record<LocalHeadroomLevel, string>,
    cases: {
      heading: "Corridas de casos con modelos locales",
      note: "La velocidad de tokens no se registra en las corridas de casos locales; mirá Pruebas de velocidad para el tok/s de cada modelo.",
      armNote: "Cada brazo es su propia fila: los brazos nunca se agrupan.",
      colRank: "#",
      colModel: "Modelo",
      colHarness: "Harness",
      colCase: "Caso",
      colArm: "Brazo",
      colRuns: "Corridas",
      colPassed: "Pasadas",
      colPassRate: "Tests graduados aprobados",
      colMedianMinutes: "Minutos medianos",
      empty: "Todavía no hay corridas de casos con modelos locales.",
      emptyAction: "Optimizá un modelo local"
    }
  }
});

function verdictBadgeClass(verdict: LocalVerdict | null | undefined): string {
  if (verdict === "ok") return "badge ok";
  if (verdict === "tight") return "badge warn";
  if (verdict === "fail") return "badge error";
  return "badge neutral";
}

function headroomBadgeClass(level: LocalHeadroomLevel | null | undefined): string {
  if (level === "near_ceiling") return "badge ok";
  if (level === "some_headroom") return "badge warn";
  if (level === "far_below") return "badge error";
  return "badge muted";
}

/**
 * Two quick rankings over local-model evidence: speed tests saved from "Tune
 * your local model", and real case runs whose executor was a local model. See
 * `src/harness_lab_app/local_tuning/results_service.py`.
 */
const COMPUTE_BOUND_QUANT = /^(q1|q2|iq1|iq2)/i;

/** MoE "30B-A3B" → active/total = 0.1; a dense "27B" or "8.2B" has no match, share 1. */
function activeShare(params: string): number {
  const match = params.match(/^([\d.]+)b-a([\d.]+)b$/i);
  if (!match) return 1;
  const total = Number(match[1]);
  const active = Number(match[2]);
  if (!total || !active) return 1;
  return active / total;
}

function computedCeiling(model: LocalReferenceModel, device: LocalReferenceDevice): number | null {
  if (!model.size_gb || !device.bandwidth_gbs) return null;
  const effectiveSizeGb = model.size_gb * activeShare(model.params);
  if (!effectiveSizeGb) return null;
  return device.bandwidth_gbs / effectiveSizeGb;
}

type ReferenceDisplayRow = {
  key: string;
  isYours: boolean;
  isComputed: boolean;
  modelLabel: string;
  hardwareLabel: string;
  machineName: string;
  writeValue: number | null;
  readValue: number | null;
  ceiling: number | null;
  fraction: number | null;
  quant: string;
  runtime: string;
  note: string;
  source: string;
  secondary: boolean;
};

function pctCeilingBadge(
  fraction: number | null,
  quant: string,
  messages: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>
) {
  if (fraction === null) return "—";
  const computeBound = COMPUTE_BOUND_QUANT.test(quant);
  const pct = Math.round(fraction * 100);
  const className = computeBound
    ? "badge muted"
    : fraction >= 0.6
      ? "badge ok"
      : fraction >= 0.35
        ? "badge warn"
        : "badge error";
  return (
    <span className={className} title={computeBound ? messages.reference.computeBoundTitle : undefined}>
      {pct}%
    </span>
  );
}

export function LocalResults() {
  const m = useMessages(MESSAGES);
  const [data, setData] = useState<LocalResultsData | null>(null);
  const [machineFilter, setMachineFilter] = useState("");
  const [referenceDevices, setReferenceDevices] = useState<LocalReferenceDevices | null>(null);
  const [selectedReferenceModelId, setSelectedReferenceModelId] = useState("");
  const [selectedDeviceId, setSelectedDeviceId] = useState("");

  useEffect(() => {
    api.local.results().then(setData).catch(() => setData({ speed: [], cases: [] }));
    api.local.referenceDevices().then(setReferenceDevices).catch(() => setReferenceDevices(null));
  }, []);

  const speed = data?.speed ?? [];
  const cases = data?.cases ?? [];
  const machines = Array.from(new Set(speed.map((row) => row.machine))).sort();
  const filteredSpeed = machineFilter
    ? speed.filter((row) => row.machine === machineFilter)
    : speed;

  const referenceModels = referenceDevices?.models ?? [];
  const referenceDeviceList = referenceDevices?.devices ?? [];
  const referenceRows = referenceDevices?.rows ?? [];
  const modelById = new Map(referenceModels.map((model) => [model.id, model]));
  const deviceById = new Map(referenceDeviceList.map((device) => [device.id, device]));

  useEffect(() => {
    if (!referenceModels.length || selectedReferenceModelId) return;
    const newestWithReference = [...speed]
      .filter((row) => row.reference_model)
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
    setSelectedReferenceModelId(newestWithReference?.reference_model ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [referenceModels.length, speed.length]);

  const selectedDevice = selectedDeviceId ? (deviceById.get(selectedDeviceId) ?? null) : null;

  const groupedDevices: Record<LocalReferenceDeviceKind, LocalReferenceDevice[]> = {
    gpu: referenceDeviceList.filter((device) => device.kind === "gpu"),
    unified: referenceDeviceList.filter((device) => device.kind === "unified"),
    cpu: referenceDeviceList.filter((device) => device.kind === "cpu")
  };

  const publishedRows = referenceRows.filter(
    (row) =>
      (!selectedReferenceModelId || row.model === selectedReferenceModelId) &&
      (!selectedDeviceId || row.device === selectedDeviceId)
  );

  const yourSpeedRows = speed.filter(
    (row) =>
      row.reference_model && (!selectedReferenceModelId || row.reference_model === selectedReferenceModelId)
  );

  const displayRows: ReferenceDisplayRow[] = [];

  for (const row of publishedRows) {
    const model = modelById.get(row.model);
    const device = deviceById.get(row.device);
    displayRows.push({
      key: `published-${row.model}-${row.device}-${row.runtime}`,
      isYours: false,
      isComputed: false,
      modelLabel: model?.label ?? row.model,
      hardwareLabel: device?.label ?? row.device,
      machineName: "",
      writeValue: row.tg,
      readValue: row.pp,
      ceiling: row.ceiling_tps,
      fraction: row.fraction,
      quant: model?.quant ?? "",
      runtime: row.runtime,
      note: row.note,
      source: row.source,
      secondary: row.secondary
    });
  }

  for (const row of yourSpeedRows) {
    const model = row.reference_model ? modelById.get(row.reference_model) : undefined;
    displayRows.push({
      key: `yours-${row.profile_id}`,
      isYours: true,
      isComputed: false,
      modelLabel: model?.label ?? row.model_id,
      hardwareLabel: row.machine,
      machineName: row.machine,
      writeValue: row.write_tokens_per_second,
      readValue: row.read_tokens_per_second,
      ceiling: row.headroom?.ceiling_tps ?? null,
      fraction: row.headroom?.fraction ?? null,
      quant: model?.quant ?? "",
      runtime: row.server_kind,
      note: row.label,
      source: "",
      secondary: false
    });
  }

  if (selectedReferenceModelId && selectedDeviceId && publishedRows.length === 0) {
    const model = modelById.get(selectedReferenceModelId);
    const device = deviceById.get(selectedDeviceId);
    if (model && device) {
      const ceiling = computedCeiling(model, device);
      if (ceiling !== null) {
        displayRows.push({
          key: "computed",
          isYours: false,
          isComputed: true,
          modelLabel: model.label,
          hardwareLabel: device.label,
          machineName: "",
          writeValue: null,
          readValue: null,
          ceiling,
          fraction: null,
          quant: model.quant,
          runtime: "",
          note: m.reference.computedNote,
          source: "",
          secondary: false
        });
      }
    }
  }

  displayRows.sort((a, b) => (b.writeValue ?? -1) - (a.writeValue ?? -1));

  return (
    <section className="page local-results">
      <header className="page-header">
        <div>
          <h1>{m.header.title}</h1>
          <p className="lede">{m.header.lede}</p>
        </div>
      </header>

      <HelpPanel title={m.help.title}>
        <p>{m.help.body}</p>
      </HelpPanel>

      <div className="panel">
        <div className="panel-head">
          <h2>{m.speed.heading}</h2>
        </div>
        <p className="muted small">{m.speed.note}</p>
        {speed.length === 0 ? (
          <EmptyState
            action={<Link to="/local/tune">{m.speed.emptyAction}</Link>}
            title={m.speed.empty}
          />
        ) : (
          <>
            <div className="toolbar">
              <select onChange={(event) => setMachineFilter(event.target.value)} value={machineFilter}>
                <option value="">{m.speed.allMachines}</option>
                {machines.map((machine) => (
                  <option key={machine} value={machine}>
                    {machine}
                  </option>
                ))}
              </select>
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{m.speed.colRank}</th>
                    <th>{m.speed.colMachine}</th>
                    <th>{m.speed.colModel}</th>
                    <th>{m.speed.colConfig}</th>
                    <th>{m.speed.colSecondsPerStep}</th>
                    <th>{m.speed.colReadSpeed}</th>
                    <th>{m.speed.colWriteSpeed}</th>
                    <th>{m.speed.colAgent}</th>
                    <th>{m.speed.colContext}</th>
                    <th>{m.speed.colFit}</th>
                    <th>{m.speed.colWindow}</th>
                    <th>{m.speed.colVsCeiling}</th>
                    <th>{m.speed.colDate}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredSpeed.map((row, index) => {
                    const fit = row.verdicts.fit;
                    const window_ = row.verdicts.window;
                    const headroom = row.headroom ?? null;
                    return (
                      <tr key={row.profile_id}>
                        <td>{index + 1}</td>
                        <td>{row.machine}</td>
                        <td>{row.model_id}</td>
                        <td>
                          {row.label}
                          {row.recommended ? (
                            <>
                              {" "}
                              <span className="badge ok" title={m.speed.recommended}>
                                ★
                              </span>
                            </>
                          ) : null}
                        </td>
                        <td>{row.seconds_per_step?.toFixed(2) ?? "—"}</td>
                        <td>{row.read_tokens_per_second?.toFixed(1) ?? "—"}</td>
                        <td>{row.write_tokens_per_second?.toFixed(1) ?? "—"}</td>
                        <td>
                          {row.agent_passed ?? "—"}/{row.agent_total ?? "—"}
                        </td>
                        <td>{row.context_length ?? "—"}</td>
                        <td>
                          {fit ? (
                            <span className={verdictBadgeClass(fit)}>{fit}</span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td>
                          {window_ ? (
                            <span className={verdictBadgeClass(window_)}>{window_}</span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td>
                          {headroom ? (
                            <span
                              className={headroomBadgeClass(headroom.level)}
                              title={`${m.headroomLevel[headroom.level]} — ${headroom.reading}`}
                            >
                              {Math.round(headroom.fraction * 100)}%
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td>{formatRunDate(row.created_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{m.reference.heading}</h2>
        </div>
        {referenceDevices ? (
          <>
            <div className="toolbar">
              <label>
                {m.reference.selectModel}{" "}
                <select
                  onChange={(event) => setSelectedReferenceModelId(event.target.value)}
                  value={selectedReferenceModelId}
                >
                  <option value="">{m.reference.allModels}</option>
                  {referenceModels.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {m.reference.selectHardware}{" "}
                <select
                  onChange={(event) => setSelectedDeviceId(event.target.value)}
                  value={selectedDeviceId}
                >
                  <option value="">{m.reference.allHardware}</option>
                  {groupedDevices.gpu.length > 0 ? (
                    <optgroup label={m.reference.groupGpu}>
                      {groupedDevices.gpu.map((device) => (
                        <option key={device.id} value={device.id}>
                          {device.label}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                  {groupedDevices.unified.length > 0 ? (
                    <optgroup label={m.reference.groupUnified}>
                      {groupedDevices.unified.map((device) => (
                        <option key={device.id} value={device.id}>
                          {device.label}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                  {groupedDevices.cpu.length > 0 ? (
                    <optgroup label={m.reference.groupCpu}>
                      {groupedDevices.cpu.map((device) => (
                        <option key={device.id} value={device.id}>
                          {device.label}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                </select>
              </label>
            </div>
            {selectedDevice ? (
              <p className="muted small">
                {m.reference.deviceSpec(
                  selectedDevice.label,
                  selectedDevice.memory,
                  selectedDevice.bandwidth_gbs !== null ? String(selectedDevice.bandwidth_gbs) : "—"
                )}
              </p>
            ) : null}
            <p className="muted small">{m.reference.note}</p>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{m.reference.colModel}</th>
                    <th>{m.reference.colHardware}</th>
                    <th>{m.reference.colWrite}</th>
                    <th>{m.reference.colRead}</th>
                    <th>{m.reference.colCeiling}</th>
                    <th>{m.reference.colPctCeiling}</th>
                    <th>{m.reference.colRuntime}</th>
                    <th>{m.reference.colNote}</th>
                    <th>{m.reference.colSource}</th>
                  </tr>
                </thead>
                <tbody>
                  {displayRows.map((row) => (
                    <tr key={row.key}>
                      <td>{row.modelLabel}</td>
                      <td>
                        {row.isYours ? (
                          <>
                            <span className="badge ok">{m.reference.vos}</span> {row.machineName}
                          </>
                        ) : (
                          row.hardwareLabel
                        )}
                      </td>
                      <td>{row.writeValue?.toFixed(1) ?? "—"}</td>
                      <td>{row.readValue?.toFixed(1) ?? "—"}</td>
                      <td>{row.ceiling?.toFixed(1) ?? "—"}</td>
                      <td>{pctCeilingBadge(row.fraction, row.quant, m)}</td>
                      <td>{row.runtime}</td>
                      <td>{row.note}</td>
                      <td>
                        {row.source ? (
                          <>
                            <a href={row.source} rel="noopener noreferrer" target="_blank">
                              {m.reference.sourceLink}
                            </a>{" "}
                            {row.secondary ? (
                              <span className="badge muted" title={m.reference.secondaryTitle}>
                                {m.reference.secondaryBadge}
                              </span>
                            ) : null}
                          </>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{m.cases.heading}</h2>
        </div>
        <p className="muted small">{m.cases.note}</p>
        <p className="muted small">{m.cases.armNote}</p>
        {cases.length === 0 ? (
          <EmptyState
            action={<Link to="/local/tune">{m.cases.emptyAction}</Link>}
            title={m.cases.empty}
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{m.cases.colRank}</th>
                  <th>{m.cases.colModel}</th>
                  <th>{m.cases.colHarness}</th>
                  <th>{m.cases.colCase}</th>
                  <th>{m.cases.colArm}</th>
                  <th>{m.cases.colRuns}</th>
                  <th>{m.cases.colPassed}</th>
                  <th>{m.cases.colPassRate}</th>
                  <th>{m.cases.colMedianMinutes}</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((row, index) => (
                  <tr key={`${row.model}-${row.harness}-${row.case_id}-${row.arm}`}>
                    <td>{index + 1}</td>
                    <td>{row.model}</td>
                    <td>{row.harness}</td>
                    <td>
                      <Link to="/evaluate/experiments">{row.case_id}</Link>
                    </td>
                    <td>{row.arm || "—"}</td>
                    <td>{row.runs}</td>
                    <td>
                      {row.passed_runs}/{row.validated_runs}
                    </td>
                    <td>
                      {row.test_pass_rate !== null ? `${Math.round(row.test_pass_rate * 100)}%` : "—"}
                    </td>
                    <td>{row.median_minutes !== null ? row.median_minutes.toFixed(1) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
