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
    expiresIn: (clock: string) => `The code expires in ${clock}.`,
    expiringSoon: "Less than a minute left — if it runs out, generate a new one.",
    regenerate: "Generate new code",
    regenerateHint: "Invalidates this code and asks for a new one.",
    connected: "Connected. Testing the connection…",
    loginFailed: "The login did not complete:",
    expired: "The code expired before it was approved. Generate a new one and enter it within its lifetime.",
    settling: "Checking whether an approval arrived in the last seconds…",
    failure: {
      rejected: "OpenAI refused the authorization.",
      timeout: "The lab ran out of time waiting for the login.",
      connection: "The runner could not reach OpenAI.",
      error: "The login did not complete."
    } as Record<string, string>,
    failureHint: {
      rejected:
        "Check that you signed in with the account that has the plan, that device code login is enabled for its workspace, and that you entered this code (not an older one).",
      timeout: "Check that the interactive worker is running (Health), then generate a new code.",
      connection: "Check the network of the runner containers, then generate a new code.",
      error: "Generate a new code; if it fails again, `make auth-codex` is the terminal fallback."
    } as Record<string, string>,
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
    expiresIn: (clock: string) => `El código vence en ${clock}.`,
    expiringSoon: "Queda menos de un minuto — si vence, generá uno nuevo.",
    regenerate: "Generar nuevo código",
    regenerateHint: "Invalida este código y pide uno nuevo.",
    connected: "Conectado. Probando la conexión…",
    loginFailed: "El login no se completó:",
    expired: "El código venció antes de aprobarse. Generá uno nuevo e ingresalo antes de que venza.",
    settling: "Verificando si llegó una aprobación en los últimos segundos…",
    failure: {
      rejected: "OpenAI rechazó la autorización.",
      timeout: "El Lab se quedó sin tiempo esperando el login.",
      connection: "El runner no pudo conectarse con OpenAI.",
      error: "El login no se completó."
    } as Record<string, string>,
    failureHint: {
      rejected:
        "Verificá que iniciaste sesión con la cuenta que tiene el plan, que el login por código de dispositivo está habilitado en su workspace y que ingresaste este código (no uno anterior).",
      timeout: "Verificá que el worker interactivo esté corriendo (Estado) y generá un código nuevo.",
      connection: "Verificá la red de los contenedores runner y generá un código nuevo.",
      error: "Generá un código nuevo; si vuelve a fallar, `make auth-codex` es la alternativa por terminal."
    } as Record<string, string>,
    notInstalled: "La imagen del runner no tiene este CLI — reconstruila (Estado)."
  }
});

const POLL_MS = 2000;
const BUSY_LOGIN = new Set(["queued", "starting", "waiting"]);
const BUSY_TEST = new Set(["queued", "running"]);

/** "14:59" — minutes and seconds, never rounded up past the real deadline. */
function formatClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * Where the code's deadline falls on this browser's clock.
 *
 * The server sends `expires_at` and its own `server_time`; the gap between
 * them is the time left when the response left the server. Anchoring that gap
 * to `Date.now()` at arrival makes the countdown immune to a skewed browser
 * clock (an absolute epoch compared with `Date.now()` is not), at the cost of
 * the request latency — the countdown can only end early, never late.
 */
function localDeadline(login: NativeConnectState, receivedAt: number): number | null {
  if (login.expires_at === undefined) {
    return null;
  }
  const reference = login.server_time ?? receivedAt / 1000;
  return receivedAt + (login.expires_at - reference) * 1000;
}

/** Re-render once a second while `active`, and return the current time. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) {
      return;
    }
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

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
  const [login, setLoginState] = useState<NativeConnectState>(cli.login ?? { state: "idle" });
  const [loginReceivedAt, setLoginReceivedAt] = useState(() => Date.now());
  const setLogin = useCallback((next: NativeConnectState) => {
    setLoginState(next);
    setLoginReceivedAt(Date.now());
  }, []);
  const [test, setTest] = useState<NativeConnectState>(cli.last_test ?? { state: "idle" });
  const [actionError, setActionError] = useState("");
  const [copied, setCopied] = useState(false);
  const autoTested = useRef(false);

  // Poll while a login is in flight — or just expired while the worker may
  // still settle an approval from its last seconds — and refresh the card
  // list once it ends.
  const loginPending =
    BUSY_LOGIN.has(login.state) || (login.state === "expired" && Boolean(login.settling));
  useEffect(() => {
    if (!loginPending) {
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
          if (!BUSY_LOGIN.has(next.state) && !next.settling) {
            void onChange();
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
    // runTest is stable enough for this effect; it only reads cli.harness.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loginPending, cli.harness, onChange, setLogin]);

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

  // `regenerate` supersedes the attempt in flight: the worker stops its
  // container and a new code is requested. One attempt at a time.
  const startLogin = (regenerate = false) => {
    setActionError("");
    setCopied(false);
    autoTested.current = false;
    setLogin({ state: "queued" });
    api
      .connectNativeCli(cli.harness, regenerate)
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
  const showingCode = login.state === "waiting" && Boolean(login.url && login.code);
  const now = useNow(showingCode);
  const deadline = showingCode ? localDeadline(login, loginReceivedAt) : null;
  const secondsLeft = deadline === null ? null : (deadline - now) / 1000;
  // The server judges expiry too (a code past its lifetime reads "expired"),
  // but the card must not show a dead code until the next poll says so.
  const codeExpired = secondsLeft !== null && secondsLeft <= 0;
  const ended = login.state === "failed" || login.state === "expired";

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
          {login.state === "idle" ? (
            <button className="primary" onClick={() => startLogin()} type="button">
              {m.enable}
            </button>
          ) : null}
          {login.state === "expired" || codeExpired ? (
            <p className="error small" role="status">
              {login.settling ? m.settling : m.expired}
            </p>
          ) : null}
          {login.state === "failed" ? (
            <div className="error small" role="status">
              <p>
                <strong>{m.failure[login.reason ?? "error"] ?? m.loginFailed}</strong>
                {login.error ? ` ${login.error}` : ""}
              </p>
              <p className="muted">{m.failureHint[login.reason ?? "error"] ?? ""}</p>
            </div>
          ) : null}
          {ended || codeExpired ? (
            <button className="primary" onClick={() => startLogin(true)} type="button">
              {m.regenerate}
            </button>
          ) : null}
          {login.state === "queued" || login.state === "starting" ? (
            <p className="muted small">{m.starting}</p>
          ) : null}
          {showingCode && !codeExpired ? (
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
                {secondsLeft !== null ? (
                  <span aria-live="off" className="native-cli-card__countdown">
                    {m.expiresIn(formatClock(secondsLeft))}
                  </span>
                ) : null}
                {secondsLeft !== null && secondsLeft < 60 ? ` ${m.expiringSoon}` : ""}
              </li>
              <li className="native-cli-card__regen">
                <button onClick={() => startLogin(true)} title={m.regenerateHint} type="button">
                  {m.regenerate}
                </button>
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
