import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";

import { api } from "../api";
import { formatRunDate } from "../dates";
import { HARNESS_NATIVE_MODELS } from "../harnesses";
import { defineMessages, useMessages } from "../i18n";
import type {
  AgyModelsState,
  ApiKeyOpState,
  ModelOption,
  NativeCliStatus,
  NativeConnectState
} from "../types";

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
 *
 * An API-key CLI (`auth === "api_key"`, the Antigravity CLI, ADR-0096) has no
 * login: its card takes a Gemini API key the operator pastes, which the worker
 * stores outside the repository and never sends back (only its last characters).
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
      gemini: "Gemini CLI",
      antigravity_cli: "Antigravity CLI"
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
      gemini: "Gemini CLI",
      antigravity_cli: "Antigravity CLI"
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

// An API-key CLI reads "connected" once a key is stored — not yet proof that
// it works, which only its connection test gives — so it groups as enabled.
function isEnabled(cli: NativeCliStatus): boolean {
  return cli.session === "connected" && !cli.unavailable;
}

/** The card for one CLI: an API-key CLI has its own; every other keeps CliCard. */
function cardFor(cli: NativeCliStatus, onChange: () => Promise<void>) {
  return cli.auth === "api_key" ? (
    <ApiKeyCliCard cli={cli} key={cli.harness} onChange={onChange} />
  ) : (
    <CliCard cli={cli} key={cli.harness} onChange={onChange} />
  );
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
            {enabled.map((cli) => cardFor(cli, load))}
          </div>
        </>
      ) : null}
      {available.length > 0 ? (
        <>
          <h4 className="native-clis__group">{m.availableTitle}</h4>
          <div className="native-clis__grid">
            {available.map((cli) => cardFor(cli, load))}
          </div>
        </>
      ) : null}
      {unavailable.length > 0 ? (
        <>
          <h4 className="native-clis__group">{m.unavailableTitle}</h4>
          <div className="native-clis__grid">
            {unavailable.map((cli) => cardFor(cli, load))}
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

/**
 * Strings of the API-key card (ADR-0096). The failure labels and hints are
 * keyed by the test record's `reason`, so each refusal says what to change.
 */
const KEY_MESSAGES = defineMessages({
  en: {
    status: {
      notConfigured: "Not configured",
      untested: "Configured — not tested yet",
      verified: "Verified",
      failed: "Test failed"
    },
    authLabel: "Gemini API key",
    provider: "Gemini API",
    billing: "API usage (per token)",
    sandbox: (value: string) => `sandbox: ${value}`,
    keyLine: (hint: string, when: string) => `Key ${hint}${when ? ` · saved ${when}` : ""}`,
    hostLane:
      "Runs are Docker-only: the runner container is the sandbox. Switch to the Docker backend.",
    tooOld: (have: string, need: string) =>
      `CLI v${have} is older than the verified v${need} — rebuild the runner image (Health).`,
    configure: "Configure API key",
    replace: "Replace key",
    keyLabel: "Gemini API key",
    keyHelp:
      "Use a dedicated, budget-capped key from Google AI Studio (or a Gemini Enterprise key). " +
      "The key is stored on the lab's backend, outside the repository; it is never shown again.",
    save: "Save",
    cancel: "Cancel",
    saving: "Saving the key…",
    keySaved: "Key saved. Testing the connection…",
    keyFailed: "The key was not saved:",
    remove: "Remove credentials",
    removing: "Removing the credentials…",
    removeConfirm:
      "Remove the Gemini API key from the lab? Its last test and model list go with it, " +
      "and runs on this harness are refused until a new key is saved.",
    model: "Model",
    test: "Test connection",
    testing: "Testing…",
    testCost: "Sends one real request to the Gemini API (billed to this key).",
    testOk: (answer: string, requested: string, apiModel: string | null, seconds: number) =>
      `Answered “${answer}” — requested ${requested || "?"}, API model ${apiModel ?? "unknown"}, ${seconds}s.`,
    testOkPlan: (answer: string, model: string, seconds: number) =>
      `Answered “${answer}” on ${model || "your plan's default model"} in ${seconds}s.`,
    failure: {
      auth: "Google rejected the API key.",
      quota: "Quota or billing exhausted, or the key is rate-limited.",
      model: "This model is not available to this key.",
      network: "The runner could not reach the Gemini API.",
      timeout: "The request took too long.",
      server: "Google's servers answered with an error (5xx).",
      no_key: "No API key is configured.",
      not_installed: "The runner image has no Antigravity CLI.",
      error: "The request did not complete."
    } as Record<string, string>,
    failureHint: {
      auth:
        "Check that the key is valid, has not been revoked as leaked, and belongs to a project " +
        "with the Generative Language API enabled — then Replace key.",
      quota:
        "Check billing and rate limits for the key's project in Google AI Studio, then retry later.",
      model: "Pick another model, or Refresh models to see the slugs this CLI version offers.",
      network: "Check that the runner containers can reach generativelanguage.googleapis.com.",
      timeout: "Retry; if it keeps timing out, check the runner's network or pick a lighter model.",
      server: "A problem on Google's side: retry later.",
      no_key: "Configure a key first.",
      not_installed: "Rebuild the runner image (Health).",
      error: "Retry; if it fails again, check the worker (Health)."
    } as Record<string, string>,
    providerSaid: "The provider said:",
    refresh: "Refresh models",
    refreshing: "Refreshing…",
    modelsCount: (count: number, version: string, when: string) =>
      `${count} models${version ? ` on v${version}` : ""}${when ? ` · refreshed ${when}` : ""}`,
    modelsList: "Model slugs",
    modelsNote:
      "Slugs this CLI version offers for the Gemini API; whether your key can use one is proven by a request.",
    modelsFailed: "Could not list the models:",
    warning:
      "Commands the agent runs can read this key: agy passes its environment to them and offers " +
      "no way to scrub it. Runs on third-party code (SWE-bench instances, external target " +
      "repositories) are refused unless HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1.",
    untrustedAllowed: "Untrusted code: allowed",
    // --- Google sign-in (ADR-0097) ---
    accountTitle: "Your Google plan",
    accountIntro:
      "Sign in with the Google account that has your Google AI plan. Runs then use the plan's " +
      "Antigravity quota, like the IDE — no API key, no per-token bill.",
    signIn: "Sign in with Google",
    signingIn: "Starting the CLI's sign-in…",
    accountStep1:
      "Open this page and sign in with the account that has your plan:",
    openSignIn: "Open Google sign-in",
    accountStep2: "Google then shows an authorization code. Paste it here:",
    codeLabel: "Authorization code",
    codePlaceholder: "4/0A…  (or the whole page address)",
    submitCode: "Connect",
    exchanging: "Checking the code with Google…",
    signInExpiresIn: (clock: string) => `This sign-in closes in ${clock}.`,
    restartSignIn: "Start over",
    accountConnected: (email: string) =>
      email ? `Signed in as ${email}` : "Signed in with Google",
    accountSaved: (when: string) => (when ? `since ${when}` : ""),
    accountBilling: "your plan's quota",
    accountProvider: "Google account",
    signOut: "Sign out",
    signOutConfirm:
      "Sign the Antigravity CLI out of the lab? The lab's copy of the sign-in is deleted; " +
      "your Google account and the IDE are not touched.",
    signInFailed: "The sign-in did not complete:",
    signInHint: {
      rejected:
        "Google refused the code: paste the code from this sign-in (each one works once, " +
        "for a few minutes), or Start over.",
      timeout:
        "Start over; if it happens again, check the interactive worker (Health).",
      error: "Start over; if it happens again, check the runner image (Health)."
    } as Record<string, string>,
    signInExpired: "The sign-in closed before a code arrived. Start over.",
    tosNote:
      "Google's Antigravity FAQ says wrapping agy in third-party applications is not supported " +
      "under consumer plans; the lab runs agy's own headless mode (agy -p) with the sign-in agy " +
      "itself performs. You decide whether to use your account for it.",
    accountWarning:
      "Commands the agent runs can read this sign-in's token in the run's home. Runs on " +
      "third-party code are refused unless HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1.",
    useKeyInstead: "Use a Gemini API key instead",
    keyFallbackNote:
      "Runs use your Google sign-in while it is connected; the key is the fallback.",
    planModelsNote: "Models your plan offers (agy models, signed in).",
    testCostAccount: "Sends one real request on your plan's quota.",
    planDefault: "Plan default"
  },
  es: {
    status: {
      notConfigured: "Sin configurar",
      untested: "Configurado — sin probar todavía",
      verified: "Verificado",
      failed: "La prueba falló"
    },
    authLabel: "clave de la API de Gemini",
    provider: "API de Gemini",
    billing: "uso de la API (por token)",
    sandbox: (value: string) => `sandbox: ${value}`,
    keyLine: (hint: string, when: string) => `Clave ${hint}${when ? ` · guardada ${when}` : ""}`,
    hostLane:
      "Las corridas son solo en Docker: el contenedor runner es el sandbox. Cambiá al backend Docker.",
    tooOld: (have: string, need: string) =>
      `El CLI v${have} es anterior a la v${need} verificada — reconstruí la imagen del runner (Estado).`,
    configure: "Configurar API key",
    replace: "Reemplazar clave",
    keyLabel: "Clave de la API de Gemini",
    keyHelp:
      "Usá una clave dedicada y con tope de presupuesto de Google AI Studio (o una clave de Gemini Enterprise). " +
      "La clave se guarda en el backend del lab, fuera del repositorio; nunca se vuelve a mostrar.",
    save: "Guardar",
    cancel: "Cancelar",
    saving: "Guardando la clave…",
    keySaved: "Clave guardada. Probando la conexión…",
    keyFailed: "La clave no se guardó:",
    remove: "Quitar credenciales",
    removing: "Quitando las credenciales…",
    removeConfirm:
      "¿Quitar la clave de la API de Gemini del lab? Se van también su última prueba y su lista de modelos, " +
      "y las corridas con este arnés se rechazan hasta que se guarde una clave nueva.",
    model: "Modelo",
    test: "Probar conexión",
    testing: "Probando…",
    testCost: "Envía un pedido real a la API de Gemini (se factura a esta clave).",
    testOk: (answer: string, requested: string, apiModel: string | null, seconds: number) =>
      `Respondió “${answer}” — pedido ${requested || "?"}, modelo de la API ${apiModel ?? "desconocido"}, ${seconds}s.`,
    testOkPlan: (answer: string, model: string, seconds: number) =>
      `Respondió “${answer}” con ${model || "el modelo predeterminado de tu plan"} en ${seconds}s.`,
    failure: {
      auth: "Google rechazó la API key.",
      quota: "Cuota o facturación agotada, o la clave tiene límite de tasa.",
      model: "Este modelo no está disponible para esta clave.",
      network: "El runner no pudo conectarse con la API de Gemini.",
      timeout: "El pedido tardó demasiado.",
      server: "Los servidores de Google respondieron con un error (5xx).",
      no_key: "No hay una API key configurada.",
      not_installed: "La imagen del runner no tiene el Antigravity CLI.",
      error: "El pedido no se completó."
    } as Record<string, string>,
    failureHint: {
      auth:
        "Verificá que la clave sea válida, que no haya sido revocada por filtrada y que pertenezca a un proyecto " +
        "con la Generative Language API habilitada — después, Reemplazar clave.",
      quota:
        "Revisá la facturación y los límites de tasa del proyecto de la clave en Google AI Studio, y reintentá más tarde.",
      model: "Elegí otro modelo, o Actualizar modelos para ver los slugs que ofrece esta versión del CLI.",
      network: "Verificá que los contenedores runner lleguen a generativelanguage.googleapis.com.",
      timeout: "Reintentá; si sigue tardando, revisá la red del runner o elegí un modelo más liviano.",
      server: "Un problema del lado de Google: reintentá más tarde.",
      no_key: "Primero configurá una clave.",
      not_installed: "Reconstruí la imagen del runner (Estado).",
      error: "Reintentá; si vuelve a fallar, revisá el worker (Estado)."
    } as Record<string, string>,
    providerSaid: "El proveedor dijo:",
    refresh: "Actualizar modelos",
    refreshing: "Actualizando…",
    modelsCount: (count: number, version: string, when: string) =>
      `${count} modelos${version ? ` en v${version}` : ""}${when ? ` · actualizados ${when}` : ""}`,
    modelsList: "Slugs de modelos",
    modelsNote:
      "Slugs que esta versión del CLI ofrece para la API de Gemini; si tu clave puede usar uno lo prueba un pedido.",
    modelsFailed: "No se pudo listar los modelos:",
    warning:
      "Los comandos que corre el agente pueden leer esta clave: agy les pasa su entorno y no ofrece " +
      "forma de limpiarlo. Las corridas sobre código de terceros (instancias de SWE-bench, repositorios " +
      "destino externos) se rechazan salvo que HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1.",
    untrustedAllowed: "Código no confiable: permitido",
    // --- Inicio de sesión con Google (ADR-0097) ---
    accountTitle: "Tu plan de Google",
    accountIntro:
      "Iniciá sesión con la cuenta de Google que tiene tu plan de Google AI. Las corridas usan la " +
      "cuota de Antigravity de tu plan, como el IDE — sin API key y sin cobro por token.",
    signIn: "Iniciar sesión con Google",
    signingIn: "Iniciando el login del CLI…",
    accountStep1:
      "Abrí esta página e iniciá sesión con la cuenta que tiene tu plan:",
    openSignIn: "Abrir el login de Google",
    accountStep2:
      "Después Google te muestra un código de autorización. Pegalo acá:",
    codeLabel: "Código de autorización",
    codePlaceholder: "4/0A…  (o la dirección completa de la página)",
    submitCode: "Conectar",
    exchanging: "Verificando el código con Google…",
    signInExpiresIn: (clock: string) =>
      `Este inicio de sesión se cierra en ${clock}.`,
    restartSignIn: "Empezar de nuevo",
    accountConnected: (email: string) =>
      email
        ? `Sesión iniciada como ${email}`
        : "Sesión iniciada con Google",
    accountSaved: (when: string) => (when ? `desde ${when}` : ""),
    accountBilling: "cuota de tu plan",
    accountProvider: "cuenta de Google",
    signOut: "Cerrar sesión",
    signOutConfirm:
      "¿Cerrar la sesión del Antigravity CLI en el lab? Se borra la copia del lab; tu cuenta de " +
      "Google y el IDE no se tocan.",
    signInFailed: "El inicio de sesión no se completó:",
    signInHint: {
      rejected:
        "Google rechazó el código: pegá el código de este inicio de sesión (cada uno sirve una vez, " +
        "por unos minutos), o Empezar de nuevo.",
      timeout:
        "Empezá de nuevo; si se repite, revisá el worker interactivo (Estado).",
      error: "Empezá de nuevo; si se repite, revisá la imagen del runner (Estado)."
    } as Record<string, string>,
    signInExpired:
      "El inicio de sesión se cerró antes de recibir un código. Empezá de nuevo.",
    tosNote:
      "La FAQ de Antigravity de Google dice que envolver agy en aplicaciones de terceros no está " +
      "soportado en planes de consumo; el lab usa el modo headless propio de agy (agy -p) con el " +
      "login que hace el mismo agy. Vos decidís si usar tu cuenta para esto.",
    accountWarning:
      "Los comandos que corre el agente pueden leer el token de esta sesión en el home de la corrida. " +
      "Las corridas sobre código de terceros se rechazan salvo que HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1.",
    useKeyInstead: "Usar una API key de Gemini en su lugar",
    keyFallbackNote:
      "Mientras la sesión de Google esté conectada, las corridas la usan; la clave queda de respaldo.",
    planModelsNote:
      "Modelos que ofrece tu plan (agy models, con sesión iniciada).",
    testCostAccount: "Envía un pedido real con la cuota de tu plan.",
    planDefault: "Predeterminado del plan"
  }
});

const BUSY_MODELS = new Set(["queued", "running"]);
const SETTLED_TEST = new Set(["ok", "failed"]);

/** "2026-11-22 10:15" for an epoch-seconds timestamp; "" when there is none. */
function formatEpoch(seconds: number | null | undefined): string {
  if (!seconds) {
    return "";
  }
  return formatRunDate(new Date(seconds * 1000).toISOString());
}

/**
 * The Antigravity CLI's own Google sign-in (ADR-0097), driven from the card.
 *
 * The worker starts agy's interactive sign-in in a runner container and
 * publishes the Google URL; the operator signs in there and pastes the code
 * Google shows. The code is read from an uncontrolled field at submit and
 * cleared at once, so it never lives in React state.
 */
function AgyAccountSection({
  cli,
  onConnected,
  onChange
}: {
  cli: NativeCliStatus;
  onConnected: () => void;
  onChange: () => Promise<void>;
}) {
  const m = useMessages(KEY_MESSAGES);
  const account = cli.account;
  const connected = Boolean(account?.connected);
  const [login, setLoginState] = useState<NativeConnectState>(cli.login ?? { state: "idle" });
  const [receivedAt, setReceivedAt] = useState(() => Date.now());
  const setLogin = useCallback((next: NativeConnectState) => {
    setLoginState(next);
    setReceivedAt(Date.now());
  }, []);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const codeInput = useRef<HTMLInputElement>(null);
  const notified = useRef(false);
  // Set once a code is handed over, until the worker reports on it: a poll
  // that lands first must not bring the form back for a second submit.
  const [codeSent, setCodeSent] = useState(false);

  const busy = BUSY_LOGIN.has(login.state);
  useEffect(() => {
    if (!busy) {
      return;
    }
    const timer = window.setInterval(() => {
      api
        .readNativeCliLogin(cli.harness)
        .then((next) => {
          setLogin(next);
          if (next.exchanging || next.state !== "waiting") {
            setCodeSent(false);
          }
          if (next.state === "connected" && !notified.current) {
            notified.current = true;
            onConnected();
          }
          if (!BUSY_LOGIN.has(next.state)) {
            void onChange();
          }
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [busy, cli.harness, onChange, onConnected, setLogin]);

  const start = (regenerate = false) => {
    setError("");
    notified.current = false;
    setCodeSent(false);
    setLogin({ state: "queued" });
    api
      .connectNativeCli(cli.harness, regenerate)
      .then(setLogin)
      .catch((exc: Error) => {
        setLogin({ state: "idle" });
        setError(exc.message);
      });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const input = codeInput.current;
    const value = input?.value.trim() ?? "";
    if (input) {
      input.value = "";
    }
    if (!value) {
      return;
    }
    setError("");
    setSubmitting(true);
    api
      .submitNativeCliCode(cli.harness, value)
      .then(() => setCodeSent(true))
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setSubmitting(false));
  };

  const signOut = () => {
    if (!window.confirm(m.signOutConfirm)) {
      return;
    }
    setError("");
    api
      .disconnectNativeCli(cli.harness)
      .then(() => {
        setLogin({ state: "idle" });
        window.setTimeout(() => void onChange(), 1500);
      })
      .catch((exc: Error) => setError(exc.message));
  };

  const waiting = login.state === "waiting" && Boolean(login.url);
  const now = useNow(waiting);
  const deadline = waiting ? localDeadline(login, receivedAt) : null;
  const secondsLeft = deadline === null ? null : (deadline - now) / 1000;
  const ended = login.state === "failed" || login.state === "expired";

  if (connected && !busy) {
    return (
      <div className="native-cli-card__account">
        <p className="native-cli-card__ok small">
          {m.accountConnected(account?.email ?? "")}{" "}
          <span className="muted">{m.accountSaved(formatEpoch(account?.saved_at))}</span>
        </p>
        <div className="native-cli-card__actions">
          <button className="link" onClick={signOut} type="button">
            {m.signOut}
          </button>
        </div>
        {error ? <p className="error error-text small">{error}</p> : null}
      </div>
    );
  }

  return (
    <div className="native-cli-card__account native-cli-card__guide">
      <p className="small">
        <strong>{m.accountTitle}</strong> — {m.accountIntro}
      </p>
      {login.state === "idle" || login.state === "connected" ? (
        <button
          className="primary"
          disabled={cli.lane_supported === false || cli.installed === false}
          onClick={() => start()}
          type="button"
        >
          {m.signIn}
        </button>
      ) : null}
      {login.state === "queued" || login.state === "starting" ? (
        <p className="muted small">{m.signingIn}</p>
      ) : null}
      {waiting ? (
        <ol className="native-cli-card__steps">
          <li>
            {m.accountStep1}{" "}
            <a href={login.url} rel="noreferrer noopener" target="_blank">
              {m.openSignIn}
            </a>
          </li>
          <li>
            {m.accountStep2}
            {login.exchanging || codeSent ? (
              <p className="muted small" role="status">
                {m.exchanging}
              </p>
            ) : (
              <form autoComplete="off" className="native-cli-card__key-form" onSubmit={submit}>
                <label>
                  <span>{m.codeLabel}</span>
                  <input
                    autoComplete="off"
                    name="google-auth-code"
                    placeholder={m.codePlaceholder}
                    ref={codeInput}
                    spellCheck={false}
                    type="password"
                  />
                </label>
                <div className="native-cli-card__actions">
                  <button className="primary" disabled={submitting} type="submit">
                    {m.submitCode}
                  </button>
                </div>
              </form>
            )}
          </li>
          <li className="muted small">
            {secondsLeft !== null && secondsLeft > 0
              ? m.signInExpiresIn(formatClock(secondsLeft))
              : null}{" "}
            <button onClick={() => start(true)} type="button">
              {m.restartSignIn}
            </button>
          </li>
        </ol>
      ) : null}
      {login.state === "failed" ? (
        <div className="error small" role="status">
          <p>
            <strong>{m.signInFailed}</strong> {login.error ?? ""}
          </p>
          <p className="muted">{m.signInHint[login.reason ?? "error"] ?? m.signInHint.error}</p>
        </div>
      ) : null}
      {login.state === "expired" ? (
        <p className="error small" role="status">
          {m.signInExpired}
        </p>
      ) : null}
      {ended ? (
        <button className="primary" onClick={() => start(true)} type="button">
          {m.restartSignIn}
        </button>
      ) : null}
      <p className="muted small">{m.tosNote}</p>
      {error ? <p className="error error-text small">{error}</p> : null}
    </div>
  );
}

/**
 * An API-key CLI (ADR-0096): Not configured → Configured → Verified / Test
 * failed. The key is read from an uncontrolled password field at submit and
 * cleared at once, so it never lives in React state; the worker stores it and
 * the card only ever sees its masked tail.
 */
function ApiKeyCliCard({ cli, onChange }: { cli: NativeCliStatus; onChange: () => Promise<void> }) {
  const shared = useMessages(MESSAGES);
  const m = useMessages(KEY_MESSAGES);
  const onAccount = cli.agy_auth === "account";
  const keyConfigured = cli.api_key?.configured ?? false;
  // Configured: a Google sign-in or a key is stored (the sign-in wins).
  const configured = onAccount || keyConfigured || cli.session === "connected";
  const [keyOp, setKeyOp] = useState<ApiKeyOpState>(cli.key_op ?? { state: "idle" });
  const [test, setTest] = useState<NativeConnectState>(cli.last_test ?? { state: "idle" });
  // The last test that finished, so a re-test does not blank the status.
  const [settled, setSettled] = useState<NativeConnectState | null>(
    cli.last_test && SETTLED_TEST.has(cli.last_test.state) ? cli.last_test : null
  );
  const [models, setModels] = useState<AgyModelsState>(cli.models ?? { state: "idle" });
  const [editing, setEditing] = useState(false);
  // Which key operation is in flight, for its progress line.
  const [pendingOp, setPendingOp] = useState<"save" | "remove" | null>(null);
  // This card saved a key and is testing it: says so until the test settles.
  const [savedNow, setSavedNow] = useState(false);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const keyInput = useRef<HTMLInputElement>(null);
  // Set when this card saved a key: once the worker stores it, test it once.
  const autoTest = useRef(false);

  const options: ModelOption[] = useMemo(() => {
    const live = models.models ?? [];
    return live.length > 0
      ? live.map((row) => ({ id: row.id, label: row.label }))
      : (HARNESS_NATIVE_MODELS[cli.harness] ?? []);
  }, [models.models, cli.harness]);
  // On the Google sign-in "" is the plan's default model.
  const [testModel, setTestModel] = useState(() =>
    onAccount ? (cli.default_test_model ?? "") : cli.default_test_model || options[0]?.id || ""
  );
  // The picked slug always renders, even when a refreshed list dropped it.
  const withPicked =
    testModel && !options.some((option) => option.id === testModel)
      ? [...options, { id: testModel, label: testModel }]
      : options;
  const shownOptions = onAccount ? [{ id: "", label: m.planDefault }, ...withPicked] : withPicked;

  useEffect(() => {
    if (SETTLED_TEST.has(test.state)) {
      setSettled(test);
      setSavedNow(false);
    }
  }, [test]);

  const runTest = useCallback(
    (model: string) => {
      setActionError("");
      setTest({ state: "queued" });
      return api
        .testNativeCli(cli.harness, model)
        .then(setTest)
        .catch((exc: Error) => {
          setTest({ state: "idle" });
          setActionError(exc.message);
        });
    },
    [cli.harness]
  );

  // A save or removal is the worker's job: poll it, then refresh the list. A
  // key this card just saved is tested once before the list reloads, so a
  // card that moves to "Enabled" remounts with that test already in flight.
  useEffect(() => {
    if (keyOp.state !== "queued") {
      return;
    }
    const timer = window.setInterval(() => {
      api
        .readCliApiKeyOp(cli.harness)
        .then((next) => {
          setKeyOp(next);
          if (next.state === "queued") {
            return;
          }
          setPendingOp(null);
          if (next.state === "saved" && autoTest.current) {
            autoTest.current = false;
            setSavedNow(true);
            void runTest(testModel).finally(() => void onChange());
            return;
          }
          autoTest.current = false;
          if (next.state === "removed") {
            setTest({ state: "idle" });
            setSettled(null);
            setModels({ state: "idle" });
          }
          void onChange();
        })
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [keyOp.state, cli.harness, onChange, runTest, testModel]);

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

  useEffect(() => {
    if (!BUSY_MODELS.has(models.state)) {
      return;
    }
    const timer = window.setInterval(() => {
      api
        .readCliModels(cli.harness)
        .then(setModels)
        .catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [models.state, cli.harness]);

  const submitKey = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const input = keyInput.current;
    const value = input?.value.trim() ?? "";
    if (input) {
      input.value = "";
    }
    if (!value) {
      return;
    }
    setFormError("");
    setActionError("");
    autoTest.current = true;
    // The previous key's verdict says nothing about this one.
    setSettled(null);
    setTest({ state: "idle" });
    setEditing(false);
    setPendingOp("save");
    setKeyOp({ state: "queued" });
    api
      .saveCliApiKey(cli.harness, value)
      .then(setKeyOp)
      .catch((exc: Error) => {
        autoTest.current = false;
        setPendingOp(null);
        setKeyOp({ state: "idle" });
        setEditing(true);
        setFormError(exc.message);
      });
  };

  const cancelEdit = () => {
    if (keyInput.current) {
      keyInput.current.value = "";
    }
    setFormError("");
    setEditing(false);
  };

  const removeKey = () => {
    if (!window.confirm(m.removeConfirm)) {
      return;
    }
    setActionError("");
    autoTest.current = false;
    setPendingOp("remove");
    setKeyOp({ state: "queued" });
    api
      .removeCliApiKey(cli.harness)
      .then(setKeyOp)
      .catch((exc: Error) => {
        setPendingOp(null);
        setKeyOp({ state: "idle" });
        setActionError(exc.message);
      });
  };

  const onSignedIn = useCallback(() => {
    setSettled(null);
    void runTest("").finally(() => void onChange());
  }, [runTest, onChange]);

  const refreshModels = () => {
    setActionError("");
    setModels({ state: "queued" });
    api
      .refreshCliModels(cli.harness)
      .then(setModels)
      .catch((exc: Error) => {
        setModels({ state: "idle" });
        setActionError(exc.message);
      });
  };

  const verdict = settled?.state;
  const status = !configured
    ? m.status.notConfigured
    : verdict === "ok"
      ? m.status.verified
      : verdict === "failed"
        ? m.status.failed
        : m.status.untested;
  const dot = !configured ? "idle" : verdict === "ok" ? "on" : verdict === "failed" ? "off" : "warn";
  const keyBusy = keyOp.state === "queued";
  const testBusy = BUSY_TEST.has(test.state);
  const modelsBusy = BUSY_MODELS.has(models.state);
  const failureKey = test.reason ?? "error";
  const liveModels = models.models ?? [];

  return (
    <article className={`native-cli-card native-cli-card--${verdict === "ok" && configured ? "on" : "idle"}`}>
      <header className="native-cli-card__head">
        <span className={`native-cli-card__dot native-cli-card__dot--${dot}`} />
        <strong>{shared.harnessLabel[cli.harness] ?? cli.harness}</strong>
        <code className="muted small">{cli.harness}</code>
      </header>
      <p className="native-cli-card__status small" role="status">
        {status}
      </p>
      <p className="muted small">
        {onAccount ? m.accountProvider : m.authLabel} · {shared.laneLabel[cli.lane] ?? cli.lane} ·{" "}
        {cli.version ? shared.version(cli.version) : shared.noVersion}
        {cli.sandbox ? ` · ${m.sandbox(cli.sandbox)}` : ""} ·{" "}
        {onAccount ? m.accountBilling : `${m.provider} · ${m.billing}`}
      </p>

      <AgyAccountSection cli={cli} onChange={onChange} onConnected={onSignedIn} />

      {!onAccount && keyConfigured && cli.api_key?.hint ? (
        <p className="muted small">
          {m.keyLine(cli.api_key.hint, formatEpoch(cli.api_key.saved_at))}
        </p>
      ) : null}

      {cli.lane_supported === false ? <p className="error error-text small">{m.hostLane}</p> : null}
      {cli.installed === false ? <p className="error error-text small">{shared.notInstalled}</p> : null}
      {cli.installed !== false && cli.too_old && cli.version ? (
        <p className="error error-text small">{m.tooOld(cli.version, cli.too_old)}</p>
      ) : null}

      {/* The key: configure, replace, remove — the alternative to the sign-in. */}
      <details className="native-cli-card__alt" open={!onAccount && (keyConfigured || editing)}>
        <summary className="small">{m.useKeyInstead}</summary>
        {onAccount && keyConfigured ? <p className="muted small">{m.keyFallbackNote}</p> : null}
        {editing ? (
          <form autoComplete="off" className="native-cli-card__key-form" onSubmit={submitKey}>
            <label>
              <span>{m.keyLabel}</span>
              <input
                autoComplete="off"
                autoFocus
                name="gemini-api-key"
                ref={keyInput}
                spellCheck={false}
                type="password"
              />
            </label>
            <p className="muted small">{m.keyHelp}</p>
            {formError ? <p className="error error-text small">{formError}</p> : null}
            <div className="native-cli-card__actions">
              <button className="primary" type="submit">
                {m.save}
              </button>
              <button onClick={cancelEdit} type="button">
                {m.cancel}
              </button>
            </div>
          </form>
        ) : (
          <div className="native-cli-card__actions">
            <button
              disabled={keyBusy}
              onClick={() => {
                setFormError("");
                setEditing(true);
              }}
              type="button"
            >
              {keyConfigured ? m.replace : m.configure}
            </button>
            {keyConfigured ? (
              <button className="danger" disabled={keyBusy} onClick={removeKey} type="button">
                {m.remove}
              </button>
            ) : null}
          </div>
        )}
        {keyBusy ? (
          <p className="muted small">{pendingOp === "remove" ? m.removing : m.saving}</p>
        ) : null}
        {savedNow && testBusy ? <p className="small">{m.keySaved}</p> : null}
        {keyOp.state === "failed" ? (
          <p className="error error-text small">
            {m.keyFailed} {keyOp.error}
          </p>
        ) : null}
      </details>

      {/* Configured: one real request proves the key serves the model. */}
      {configured ? (
        <>
          <div className="native-cli-card__actions">
            <select
              aria-label={m.model}
              className="native-cli-card__model"
              disabled={testBusy}
              onChange={(event) => setTestModel(event.target.value)}
              value={testModel}
            >
              {shownOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
            <button disabled={testBusy || keyBusy} onClick={() => void runTest(testModel)} type="button">
              {testBusy ? m.testing : m.test}
            </button>
          </div>
          <span className="muted small">{onAccount ? m.testCostAccount : m.testCost}</span>
        </>
      ) : null}
      {test.state === "ok" ? (
        <p className="native-cli-card__ok small">
          {test.auth === "account"
            ? m.testOkPlan(test.answer ?? "", test.requested_model || test.model || "", test.seconds ?? 0)
            : m.testOk(
                test.answer ?? "",
                test.requested_model ?? test.model ?? "",
                test.api_model ?? null,
                test.seconds ?? 0
              )}
        </p>
      ) : null}
      {test.state === "failed" ? (
        <div className="native-cli-card__failure small" role="status">
          <p className="error-text">
            <strong>{m.failure[failureKey] ?? m.failure.error}</strong>
          </p>
          <p className="muted">{m.failureHint[failureKey] ?? m.failureHint.error}</p>
          {test.error ? (
            <p className="muted">
              {m.providerSaid} <span className="mono">{test.error}</span>
            </p>
          ) : null}
        </div>
      ) : null}

      {/* The CLI's own model list for this key. */}
      {configured ? (
        <div className="native-cli-card__models">
          <div className="native-cli-card__actions">
            <button disabled={modelsBusy || keyBusy} onClick={refreshModels} type="button">
              {modelsBusy ? m.refreshing : m.refresh}
            </button>
            {models.state === "ok" && liveModels.length > 0 ? (
              <span className="muted small">
                {m.modelsCount(
                  liveModels.length,
                  models.cli_version ?? "",
                  formatEpoch(models.refreshed_at)
                )}
              </span>
            ) : null}
          </div>
          {models.state === "failed" ? (
            <p className="error error-text small">
              {m.modelsFailed} {models.error}
            </p>
          ) : null}
          {liveModels.length > 0 ? (
            <details>
              <summary className="small">{m.modelsList}</summary>
              <ul className="small">
                {liveModels.map((row) => (
                  <li key={row.id}>
                    <code>{row.id}</code> — {row.label} → <code>{row.api_model ?? "?"}</code>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {liveModels.length > 0 ? (
            <p className="muted small">{onAccount ? m.planModelsNote : m.modelsNote}</p>
          ) : null}
        </div>
      ) : null}

      <div className="callout callout--warning native-cli-card__warning small">
        <p>{onAccount ? m.accountWarning : m.warning}</p>
        {cli.untrusted_allowed ? (
          <p>
            <strong>{m.untrustedAllowed}</strong> (HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1)
          </p>
        ) : null}
      </div>

      {actionError ? <p className="error error-text small">{actionError}</p> : null}
    </article>
  );
}
