import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { defineMessages, useMessages } from "../../i18n";
import type { ModelOption, NativeCliStatus, NativeModelRecord } from "../../types";
import { InfoTooltip } from "../InfoTooltip";
import { armFor, encodeArm, GUIDE_HARNESSES, loadClis, loadModels, loadNative, runModelId } from "./guideData";
import type { Harness } from "./guideData";

/**
 * Pick one arm of a guided run: the agent CLI first, then one of its models
 * (ADR-0093, amended 2026-11-20).
 *
 * The flat list it replaces mixed every gateway card, local host and CLI model
 * in one wall of cards, and paired a model with a harness behind the person's
 * back. Here the harness is a tab that says whether it can run right now, and
 * the cards under it are only what that harness can drive: opencode the
 * gateway and local hosts (by where they run, then who makes them), claude,
 * codex and gemini their own CLI's models. A model the run would be refused
 * for is shown, disabled, with the reason — never silently missing.
 */

type Status = { tone: "ok" | "warn" | "off"; text: string; detail?: string };

type Card = {
  id: string;
  label: string;
  sub: string;
  disabled?: string;
  tip?: string;
  /** claude only: the effort levels this model accepts; empty = none. */
  efforts?: string[];
};

const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

const MESSAGES = defineMessages({
  en: {
    harnessTips: {
      opencode:
        "Open-source agent. Drives any model behind the lab's AI gateway (cloud cards) or straight on a local host (LM Studio, Ollama).",
      claude: "Claude Code, the CLI, signed in with the lab's Claude token. Its own models.",
      codex: "OpenAI Codex CLI on your ChatGPT plan (make auth-codex). Its own models.",
      gemini: "Google Gemini CLI on your Google sign-in (make auth-gemini). Its own models."
    } as Record<Harness, string>,
    ready: "ready",
    gateway: "gateway",
    signIn: "sign in first",
    unavailable: "unavailable",
    notInstalled: "not in the runner image",
    unknown: "status unknown",
    checking: "checking…",
    whereLocal: "Local · this machine",
    whereGateway: "Through the gateway",
    current: "current",
    previous: "previous",
    loaded: (ctx: string) => `loaded · ${ctx} ctx`,
    notLoaded: "not loaded · loads on first call",
    cloud: "cloud",
    localCard: "local · via gateway",
    noCard: "not on your gateway",
    noCardWhy: (model: string) =>
      `The gateway has no card for ${model}: every run would die on its first request. Add it to the gateway, or pick another.`,
    notRoutable: "not routable",
    notRoutableWhy: "opencode has no provider entry for this id. Enable it under Settings before running it.",
    unreachable: "host not serving it",
    signInBody: (harness: string, command: string) =>
      `${harness} runs on your own subscription and is not signed in yet. Sign in once (${command}, or the button in System → Agent CLIs); every run reuses the session.`,
    openClis: "Open Agent CLIs",
    unavailableBody: (reason: string) => `${reason}`,
    noModels: "No models from this source right now.",
    taken: "already chosen",
    reload: "⟳ Reload",
    reloading: "Reloading…",
    effort: "Effort",
    effortDefault: "Default",
    effortTip:
      "Reasoning effort, passed to Claude Code as --effort. Low is fastest and uses the fewest tokens; max is the most thorough. Default lets the CLI decide. Opus at high and Opus at low are two arms you can compare.",
    noEffort: "no effort control"
  },
  es: {
    harnessTips: {
      opencode:
        "Agente open source. Maneja cualquier modelo detrás del AI gateway del lab (cards en la nube) o directo en un host local (LM Studio, Ollama).",
      claude: "Claude Code, la CLI, con el token de Claude del lab. Sus propios modelos.",
      codex: "La CLI de OpenAI Codex con tu plan de ChatGPT (make auth-codex). Sus propios modelos.",
      gemini: "La CLI de Google Gemini con tu cuenta de Google (make auth-gemini). Sus propios modelos."
    } as Record<Harness, string>,
    ready: "lista",
    gateway: "gateway",
    signIn: "falta iniciar sesión",
    unavailable: "no disponible",
    notInstalled: "no está en la imagen",
    unknown: "estado desconocido",
    checking: "consultando…",
    whereLocal: "Local · esta máquina",
    whereGateway: "Por el gateway",
    current: "actual",
    previous: "anterior",
    loaded: (ctx: string) => `cargado · ${ctx} ctx`,
    notLoaded: "sin cargar · se carga en el primer pedido",
    cloud: "nube",
    localCard: "local · vía gateway",
    noCard: "no está en tu gateway",
    noCardWhy: (model: string) =>
      `El gateway no tiene card para ${model}: cada corrida moriría en el primer pedido. Agregalo al gateway, o elegí otro.`,
    notRoutable: "no enrutable",
    notRoutableWhy: "opencode no tiene entrada de proveedor para este id. Habilitalo en Configuración antes de usarlo.",
    unreachable: "el host no lo sirve",
    signInBody: (harness: string, command: string) =>
      `${harness} corre con tu propia suscripción y todavía no inició sesión. Iniciala una vez (${command}, o el botón en Sistema → CLIs de agentes); todas las corridas reusan la sesión.`,
    openClis: "Abrir CLIs de agentes",
    unavailableBody: (reason: string) => `${reason}`,
    noModels: "No hay modelos de esta fuente ahora.",
    taken: "ya elegido",
    reload: "⟳ Recargar",
    reloading: "Recargando…",
    effort: "Esfuerzo",
    effortDefault: "Por defecto",
    effortTip:
      "Esfuerzo de razonamiento, pasado a Claude Code como --effort. Low es lo más rápido y gasta menos tokens; max es lo más exhaustivo. Por defecto decide la CLI. Opus en high y Opus en low son dos brazos que podés comparar.",
    noEffort: "sin control de esfuerzo"
  }
});

// Vendor order inside opencode's sources: local hosts lead, "other" trails.
const PROVIDER_ORDER = ["lmstudio", "ollama", "anthropic", "openai", "google", "nvidia", "other"];

function compactTokens(tokens: number): string {
  return tokens >= 1000 ? `${Math.round(tokens / 1000)}k` : String(tokens);
}

function cliStatus(
  cli: NativeCliStatus | undefined,
  loaded: boolean,
  m: (typeof MESSAGES)["en"]
): Status {
  if (!loaded) return { tone: "warn", text: m.checking };
  if (!cli) return { tone: "warn", text: m.unknown };
  if (cli.installed === false) return { tone: "off", text: m.notInstalled };
  if (cli.unavailable) return { tone: "warn", text: m.unavailable, detail: cli.unavailable };
  if (cli.billing_mode === "gateway") return { tone: "ok", text: m.gateway };
  if (cli.session !== "connected") return { tone: "off", text: m.signIn };
  return { tone: "ok", text: m.ready };
}

export function HarnessModelChooser({
  value,
  onPick,
  exclude = []
}: {
  /** The current answer, `harness|model`, or "" before one is picked. */
  value: string;
  onPick: (value: string) => void;
  /** Answers already taken by an earlier arm: shown, not pickable. */
  exclude?: string[];
}) {
  const m = useMessages(MESSAGES);
  const picked = value ? armFor(value) : null;
  const [harness, setHarness] = useState<Harness>(picked?.harness ?? "opencode");
  const [gatewayModels, setGatewayModels] = useState<ModelOption[]>([]);
  const [native, setNative] = useState<Record<string, NativeModelRecord[]>>({});
  const [clis, setClis] = useState<NativeCliStatus[] | null>(null);
  const [clisLoaded, setClisLoaded] = useState(false);
  const [where, setWhere] = useState<"direct" | "gateway">("direct");
  // Chosen before the model, so one click on a card picks both.
  const [effort, setEffort] = useState(picked?.effort ?? "");
  const [provider, setProvider] = useState("");

  useEffect(() => {
    loadModels().then(setGatewayModels);
    loadNative().then(setNative);
    loadClis().then((rows) => {
      setClis(rows);
      setClisLoaded(true);
    });
  }, []);

  // A picked opencode model opens on its own source and vendor.
  useEffect(() => {
    if (!picked || picked.harness !== "opencode") return;
    const row = gatewayModels.find((model) => model.id === picked.model);
    if (row?.source) setWhere(row.source);
    if (row?.provider) setProvider(row.provider);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gatewayModels.length]);

  const statusOf = (name: Harness) => cliStatus(clis?.find((cli) => cli.harness === name), clisLoaded, m);
  const status = statusOf(harness);

  const pool = useMemo(
    () => gatewayModels.filter((model) => (model.source ?? "gateway") === where),
    [gatewayModels, where]
  );
  const providers = useMemo(() => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const model of pool) {
      const id = model.provider ?? "other";
      const now = counts.get(id);
      counts.set(id, { label: model.provider_label ?? id, count: (now?.count ?? 0) + 1 });
    }
    return PROVIDER_ORDER.filter((id) => counts.has(id)).map((id) => ({ id, ...counts.get(id)! }));
  }, [pool]);
  const activeProvider = providers.some((p) => p.id === provider) ? provider : (providers[0]?.id ?? "");

  const cards: Card[] = useMemo(() => {
    if (harness === "opencode") {
      return pool
        .filter((model) => (model.provider ?? "other") === activeProvider)
        .map((model) => {
          const direct = model.source === "direct";
          const name = direct ? model.id.replace(/^[a-z]+\//, "") : runModelId(model.id);
          const sub = direct
            ? model.loaded
              ? m.loaded(model.loaded_context ? compactTokens(model.loaded_context) : "?")
              : m.notLoaded
            : /lmstudio|ollama/.test(model.provider ?? "")
              ? m.localCard
              : m.cloud;
          const disabled =
            model.routable === false ? m.notRoutable : model.reachable === false ? m.unreachable : undefined;
          return {
            id: model.id,
            label: name,
            sub,
            disabled,
            tip:
              model.routable === false
                ? m.notRoutableWhy
                : model.reachable === false
                  ? (model.unreachable_reason ?? m.unreachable)
                  : model.label !== name
                    ? model.label
                    : undefined
          };
        });
    }
    return (native[harness] ?? []).map((model) => {
      // An older API (or the offline list) does not say: offer every level and
      // let the server's preflight be the judge, as the run form does.
      const efforts = harness === "claude" ? (model.effort_levels ?? EFFORTS) : [];
      const generation = model.generation === "previous" ? m.previous : m.current;
      return {
        id: model.id,
        label: model.label.replace(/ \(previous\)$/, ""),
        sub: effort && harness === "claude" && efforts.length === 0 ? `${generation} · ${m.noEffort}` : generation,
        disabled: model.on_gateway === false ? m.noCard : undefined,
        tip: model.on_gateway === false ? m.noCardWhy(model.id) : model.id,
        efforts
      };
    });
  }, [harness, pool, activeProvider, native, m, effort]);

  const blocked = status.tone === "off";
  const login = harness === "codex" ? "make auth-codex" : "make auth-gemini";

  return (
    <div className="hm-chooser">
      <div aria-label="harness" className="hm-tabs" role="tablist">
        {GUIDE_HARNESSES.map((name) => {
          const s = statusOf(name);
          return (
            <button
              aria-selected={harness === name}
              className={harness === name ? "hm-tab hm-tab--active" : "hm-tab"}
              key={name}
              onClick={() => setHarness(name)}
              role="tab"
              title={m.harnessTips[name]}
              type="button"
            >
              <strong>{name}</strong>
              <span className={`hm-dot hm-dot--${s.tone}`}>{s.text}</span>
            </button>
          );
        })}
      </div>
      <p className="muted guide-small hm-about">
        {m.harnessTips[harness]}
      </p>

      {blocked ? (
        <div className="callout callout--warning">
          <p>
            {status.text === m.signIn ? m.signInBody(harness, login) : status.text}
          </p>
          <Link to="/system/agent-clis">{m.openClis}</Link>
        </div>
      ) : (
        <>
          {status.detail ? <p className="guide-warn">{m.unavailableBody(status.detail)}</p> : null}
          {harness === "opencode" ? (
            <div className="hm-filters">
              <div className="segmented" role="group">
                {(["direct", "gateway"] as const).map((id) => (
                  <button
                    aria-pressed={where === id}
                    className={where === id ? "active" : ""}
                    key={id}
                    onClick={() => setWhere(id)}
                    type="button"
                  >
                    {id === "direct" ? m.whereLocal : m.whereGateway}
                  </button>
                ))}
              </div>
              <div className="chip-row" role="group">
                {providers.map((entry) => (
                  <button
                    aria-pressed={entry.id === activeProvider}
                    className={`chip${entry.id === activeProvider ? " active" : ""}`}
                    key={entry.id}
                    onClick={() => setProvider(entry.id)}
                    type="button"
                  >
                    {entry.label} <span className="chip-count">{entry.count}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {harness === "claude" ? (
            <div className="hm-effort">
              <span className="guide-field-label">
                {m.effort} <InfoTooltip text={m.effortTip} />
              </span>
              <div className="segmented" role="group">
                {["", ...EFFORTS].map((level) => (
                  <button
                    aria-pressed={effort === level}
                    className={effort === level ? "active" : ""}
                    key={level || "default"}
                    onClick={() => setEffort(level)}
                    type="button"
                  >
                    {level || m.effortDefault}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {cards.length === 0 ? <p className="muted">{m.noModels}</p> : null}
          <div className="guide-options hm-models">
            {cards.map((card) => {
              // A model without effort control runs at the CLI default.
              const answer = encodeArm(harness, card.id, card.efforts?.includes(effort) ? effort : "");
              const taken = exclude.includes(answer);
              const pressed = value === answer;
              const why = card.disabled ?? (taken ? m.taken : undefined);
              return (
                <div
                  className="guide-option"
                  data-disabled={Boolean(why) || undefined}
                  data-pressed={pressed}
                  key={card.id}
                >
                  <button
                    aria-pressed={pressed}
                    className="guide-option-hit"
                    disabled={Boolean(why)}
                    onClick={() => onPick(answer)}
                    type="button"
                  >
                    <strong>{card.label}</strong>
                    <span className="muted">{card.sub}</span>
                    {why ? <span className="guide-tag guide-tag--warn">{why}</span> : null}
                  </button>
                  {card.tip ? (
                    <span className="guide-option-info">
                      <InfoTooltip text={card.tip} />
                    </span>
                  ) : null}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

/** How an arm reads in the trail, the diagram and the review: `claude · Claude Opus 5.5`. */
export function armLabel(value: string, models: ModelOption[], native: Record<string, NativeModelRecord[]>): string {
  if (!value) return "";
  const arm = armFor(value);
  if (arm.harness === "opencode") {
    const row = models.find((model) => model.id === arm.model);
    const name = row?.source === "direct" ? arm.model.replace(/^[a-z]+\//, "") : runModelId(arm.model);
    return `opencode · ${name}`;
  }
  const row = (native[arm.harness] ?? []).find((model) => model.id === arm.model);
  const name = row?.label.replace(/ \(previous\)$/, "") ?? arm.model;
  return `${arm.harness} · ${name}${arm.effort ? ` · ${arm.effort}` : ""}`;
}
