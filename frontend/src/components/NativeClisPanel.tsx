import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { NativeCliStatus, NativeConnectState } from "../types";

/**
 * The agent CLIs a run can drive, as cards (System → Agent CLIs, ADR-0094).
 *
 * Enabled harnesses come first, each with a "Test connection" button that sends
 * one real request with exactly the credential a run uses — a credential file
 * is not proof the provider serves the account (Gemini CLI kept a valid login
 * after Google stopped serving personal plans). The rest come second, each with
 * a guided "Enable": for a subscription CLI the worker starts the CLI's own
 * device login and this card shows the URL and the one-time code to approve in
 * the operator's browser. The lab never sees a password or a token value.
 */
const MESSAGES = defineMessages({
  en: {
    title: "Agent CLIs",
    intro:
      "The CLIs a run can drive. Enabled ones can be tested with one real request; " +
      "the others are enabled here, step by step — no terminal needed.",
    enabledTitle: "Enabled",
    availableTitle: "Available to enable",
    unavailableTitle: "Not available",
    harnessLabel: {
      opencode: "OpenCode",
      claude: "Claude Code",
      codex: "Codex CLI",
      gemini: "Gemini CLI"
    } as Record<string, string>,
    authLabel: {
      gateway: "through your gateway",
      token: "Claude token in .env",
      subscription: "your plan's login",
      unknown: "unknown"
    } as Record<string, string>,
    laneLabel: { docker: "runner container", host: "host" } as Record<string, string>,
    version: (v: string) => `v${v}`,
    noVersion: "version unknown",
    billing: {
      subscription: "subscription quota",
      api: "per-token API",
      gateway: "gateway",
      unknown: "unknown"
    } as Record<string, string>,
    test: "Test connection",
    testing: "Testing…",
    testOk: (model: string, seconds: number) =>
      `Answered${model ? ` (${model})` : ""} in ${seconds}s.`,
    testFailed: "The provider refused the request:",
    testCost: "Spends one request of your plan.",
    enable: "Enable",
    disconnect: "Disconnect",
    howToEnable: {
      opencode: "Set GATEWAY_BASE_URL and GATEWAY_TOKEN in Settings.",
      claude:
        "Run `claude setup-token` and paste the token as CLAUDE_CODE_OAUTH_TOKEN in Settings."
    } as Record<string, string>,
    goToSettings: "Open Settings",
    starting: "Starting the CLI's login…",
    step1: "Open this page and sign in with the account that has your plan:",
    step2: "Enter this one-time code:",
    copy: "Copy",
    copied: "Copied",
    waitingApproval: "Waiting for you to approve it in the browser…",
    expiresIn: (minutes: number) => `The code expires in ${minutes} min.`,
    connected: "Connected. Testing the connection…",
    loginFailed: "The login did not complete:",
    retry: "Try again",
    notInstalled: "The runner image has no CLI for it — rebuild the image (Health)."
  },
  es: {
    title: "CLIs de agentes",
    intro:
      "Los CLIs que una corrida puede usar. Los habilitados se prueban con un pedido " +
      "real; los demás se habilitan acá, paso a paso — sin terminal.",
    enabledTitle: "Habilitados",
    availableTitle: "Disponibles para habilitar",
    unavailableTitle: "No disponibles",
    harnessLabel: {
      opencode: "OpenCode",
      claude: "Claude Code",
      codex: "Codex CLI",
      gemini: "Gemini CLI"
    } as Record<string, string>,
    authLabel: {
      gateway: "por tu gateway",
      token: "token de Claude en .env",
      subscription: "el login de tu plan",
      unknown: "desconocido"
    } as Record<string, string>,
    laneLabel: { docker: "contenedor runner", host: "host" } as Record<string, string>,
    version: (v: string) => `v${v}`,
    noVersion: "versión desconocida",
    billing: {
      subscription: "cuota de suscripción",
      api: "API por token",
      gateway: "gateway",
      unknown: "desconocida"
    } as Record<string, string>,
    test: "Probar conexión",
    testing: "Probando…",
    testOk: (model: string, seconds: number) =>
      `Respondió${model ? ` (${model})` : ""} en ${seconds}s.`,
    testFailed: "El proveedor rechazó el pedido:",
    testCost: "Gasta un pedido de tu plan.",
    enable: "Habilitar",
    disconnect: "Desconectar",
    howToEnable: {
      opencode: "Cargá GATEWAY_BASE_URL y GATEWAY_TOKEN en Configuración.",
      claude:
        "Corré `claude setup-token` y pegá el token como CLAUDE_CODE_OAUTH_TOKEN en Configuración."
    } as Record<string, string>,
    goToSettings: "Abrir Configuración",
    starting: "Iniciando el login del CLI…",
    step1: "Abrí esta página e iniciá sesión con la cuenta que tiene tu plan:",
    step2: "Ingresá este código de un solo uso:",
    copy: "Copiar",
    copied: "Copiado",
    waitingApproval: "Esperando que lo apruebes en el navegador…",
    expiresIn: (minutes: number) => `El código vence en ${minutes} min.`,
    connected: "Conectado. Probando la conexión…",
    loginFailed: "El login no se completó:",
    retry: "Reintentar",
    notInstalled: "La imagen del runner no tiene este CLI — reconstruila (Estado)."
  }
});

const POLL_MS = 2000;
const BUSY_LOGIN = new Set(["queued", "starting", "waiting"]);
const BUSY_TEST = new Set(["queued", "running"]);

function isEnabled(cli: NativeCliStatus): boolean {
  return cli.session === "connected" && !cli.unavailable;
}

export function NativeClisPanel() {
  const m = useMessages(MESSAGES);
  const [clis, setClis] = useState<NativeCliStatus[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    return api
      .listNativeClis()
      .then((data) => {
        setClis(data.clis);
        setError("");
      })
      .catch((exc: Error) => setError(exc.message));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return <p className="error">{error}</p>;
  }
  if (clis === null) {
    return null;
  }
  const enabled = clis.filter(isEnabled);
  const available = clis.filter((cli) => !isEnabled(cli) && !cli.unavailable);
  const unavailable = clis.filter((cli) => cli.unavailable);

  return (
    <section className="native-clis">
      <h3>{m.title}</h3>
      <p className="muted">{m.intro}</p>
      {enabled.length > 0 ? (
        <>
          <h4 className="native-clis__group">{m.enabledTitle}</h4>
          <div className="native-clis__grid">
            {enabled.map((cli) => (
              <CliCard cli={cli} key={cli.harness} onChange={load} />
            ))}
          </div>
        </>
      ) : null}
      {available.length > 0 ? (
        <>
          <h4 className="native-clis__group">{m.availableTitle}</h4>
          <div className="native-clis__grid">
            {available.map((cli) => (
              <CliCard cli={cli} key={cli.harness} onChange={load} />
            ))}
          </div>
        </>
      ) : null}
      {unavailable.length > 0 ? (
        <>
          <h4 className="native-clis__group">{m.unavailableTitle}</h4>
          <div className="native-clis__grid">
            {unavailable.map((cli) => (
              <CliCard cli={cli} key={cli.harness} onChange={load} />
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}

function CliCard({ cli, onChange }: { cli: NativeCliStatus; onChange: () => Promise<void> }) {
  const m = useMessages(MESSAGES);
  const enabled = isEnabled(cli);
  const [login, setLogin] = useState<NativeConnectState>(cli.login ?? { state: "idle" });
  const [test, setTest] = useState<NativeConnectState>(cli.last_test ?? { state: "idle" });
  const [actionError, setActionError] = useState("");
  const [copied, setCopied] = useState(false);
  const autoTested = useRef(false);

  // Poll while a login or a test is in flight; refresh the card list when one ends.
  useEffect(() => {
    if (!BUSY_LOGIN.has(login.state)) {
      return;
    }
    const timer = window.setInterval(() => {
      api
        .readNativeCliLogin(cli.harness)
        .then((next) => {
          setLogin(next);
          if (next.state === "connected" && !autoTested.current) {
            autoTested.current = true;
            void runTest();
          }
          if (!BUSY_LOGIN.has(next.state)) {
            void onChange();
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
    // runTest is stable enough for this effect; it only reads cli.harness.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [login.state, cli.harness, onChange]);

  useEffect(() => {
    if (!BUSY_TEST.has(test.state)) {
      return;
    }
    const timer = window.setInterval(() => {
      api
        .readNativeCliTest(cli.harness)
        .then(setTest)
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [test.state, cli.harness]);

  const runTest = () => {
    setActionError("");
    setTest({ state: "queued" });
    return api
      .testNativeCli(cli.harness)
      .then(setTest)
      .catch((exc: Error) => {
        setTest({ state: "idle" });
        setActionError(exc.message);
      });
  };

  const startLogin = () => {
    setActionError("");
    autoTested.current = false;
    setLogin({ state: "queued" });
    api
      .connectNativeCli(cli.harness)
      .then(setLogin)
      .catch((exc: Error) => {
        setLogin({ state: "idle" });
        setActionError(exc.message);
      });
  };

  const disconnect = () => {
    setActionError("");
    api
      .disconnectNativeCli(cli.harness)
      .then(() => {
        setLogin({ state: "idle" });
        setTest({ state: "idle" });
        window.setTimeout(() => void onChange(), 1500);
      })
      .catch((exc: Error) => setActionError(exc.message));
  };

  const copyCode = (code: string) => {
    void navigator.clipboard?.writeText(code).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const tone = cli.unavailable ? "off" : enabled ? "on" : "idle";
  const minutesLeft = login.expires_at
    ? Math.max(0, Math.round((login.expires_at * 1000 - Date.now()) / 60000))
    : null;

  return (
    <article className={`native-cli-card native-cli-card--${tone}`}>
      <header className="native-cli-card__head">
        <span className={`native-cli-card__dot native-cli-card__dot--${tone}`} />
        <strong>{m.harnessLabel[cli.harness] ?? cli.harness}</strong>
        <code className="muted small">{cli.harness}</code>
      </header>
      <p className="muted small">
        {m.authLabel[cli.auth ?? "unknown"]} · {m.laneLabel[cli.lane] ?? cli.lane} ·{" "}
        {cli.version ? m.version(cli.version) : m.noVersion}
        {cli.sandbox ? ` · sandbox: ${cli.sandbox}` : ""} ·{" "}
        {m.billing[cli.billing_mode] ?? cli.billing_mode}
      </p>

      {cli.unavailable ? <p className="small">{cli.unavailable}</p> : null}

      {!cli.unavailable && cli.installed === false && cli.harness !== "opencode" ? (
        <p className="error small">{m.notInstalled}</p>
      ) : null}

      {/* Enabled: one real request proves it. */}
      {cli.testable && (enabled || cli.unavailable) ? (
        <div className="native-cli-card__actions">
          <button
            disabled={BUSY_TEST.has(test.state)}
            onClick={() => void runTest()}
            type="button"
          >
            {BUSY_TEST.has(test.state) ? m.testing : m.test}
          </button>
          <span className="muted small">{m.testCost}</span>
        </div>
      ) : null}
      {test.state === "ok" ? (
        <p className="native-cli-card__ok small">
          {m.testOk(test.model ?? "", test.seconds ?? 0)}
        </p>
      ) : null}
      {test.state === "failed" ? (
        <p className="error small">
          {m.testFailed} {test.error}
        </p>
      ) : null}

      {/* Not enabled yet: a guided step. */}
      {!enabled && !cli.unavailable && cli.connectable ? (
        <div className="native-cli-card__guide">
          {login.state === "idle" || login.state === "failed" ? (
            <button className="primary" onClick={startLogin} type="button">
              {login.state === "failed" ? m.retry : m.enable}
            </button>
          ) : null}
          {login.state === "failed" && login.error ? (
            <p className="error small">
              {m.loginFailed} {login.error}
            </p>
          ) : null}
          {login.state === "queued" || login.state === "starting" ? (
            <p className="muted small">{m.starting}</p>
          ) : null}
          {login.state === "waiting" && login.url && login.code ? (
            <ol className="native-cli-card__steps">
              <li>
                {m.step1}{" "}
                <a href={login.url} rel="noreferrer noopener" target="_blank">
                  {login.url}
                </a>
              </li>
              <li>
                {m.step2} <code className="native-cli-card__code">{login.code}</code>{" "}
                <button onClick={() => copyCode(login.code ?? "")} type="button">
                  {copied ? m.copied : m.copy}
                </button>
              </li>
              <li className="muted small">
                {m.waitingApproval}{" "}
                {minutesLeft !== null ? m.expiresIn(minutesLeft) : ""}
              </li>
            </ol>
          ) : null}
          {login.state === "connected" ? <p className="small">{m.connected}</p> : null}
        </div>
      ) : null}

      {!enabled && !cli.unavailable && !cli.connectable ? (
        <div className="native-cli-card__guide">
          <p className="small">{m.howToEnable[cli.harness] ?? ""}</p>
          <a href="/system/settings">{m.goToSettings}</a>
        </div>
      ) : null}

      {enabled && cli.connectable ? (
        <div className="native-cli-card__actions">
          <button className="link" onClick={disconnect} type="button">
            {m.disconnect}
          </button>
        </div>
      ) : null}

      {actionError ? <p className="error small">{actionError}</p> : null}
    </article>
  );
}
