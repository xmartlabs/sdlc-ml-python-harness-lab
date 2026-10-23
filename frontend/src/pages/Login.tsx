import { useState } from "react";
import type { ReactNode } from "react";

import { api, ApiError } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { XmartlabsLogo } from "../components/XmartlabsLogo";
import { defineMessages, useMessages } from "../i18n";

type LoginProps = {
  onAuthenticated: () => void;
};

const MESSAGES = defineMessages({
  en: {
    unreachable: "Could not reach the API.",
    intro: "Sign in to manage runs, models and skills.",
    password: "Password",
    signingIn: "Signing in…",
    signIn: "Sign in",
    lostPassword: (cmd: ReactNode) => <>Lost the password? Run {cmd} on the server.</>
  },
  es: {
    unreachable: "No se pudo acceder a la API.",
    intro: "Iniciá sesión para administrar corridas, modelos y skills.",
    password: "Contraseña",
    signingIn: "Iniciando sesión…",
    signIn: "Iniciar sesión",
    lostPassword: (cmd: ReactNode) => <>¿Perdiste la contraseña? Corré {cmd} en el servidor.</>
  }
});

/**
 * Single-user gate (ADR-0015). There is no username: the lab has one operator,
 * and the password is set from the server with `make set-password`.
 */
export function Login({ onAuthenticated }: LoginProps) {
  const m = useMessages(MESSAGES);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      onAuthenticated();
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : m.unreachable);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-shell">
      <form className="panel login-panel" onSubmit={submit}>
        <div className="login-logo-wrap">
          <XmartlabsLogo height={32} width={160} />
        </div>
        <div className="panel-head login-panel-head">
          <h2>Agentic Harness Lab</h2>
        </div>
        <p className="muted">{m.intro}</p>
        {error !== null ? <ErrorBanner message={error} /> : null}
        <label>
          <span>{m.password}</span>
          <input
            autoFocus
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>
        <button className="primary" disabled={busy || password === ""} type="submit">
          {busy ? m.signingIn : m.signIn}
        </button>
        <p className="muted">{m.lostPassword(<code>make set-password</code>)}</p>
      </form>
    </main>
  );
}
