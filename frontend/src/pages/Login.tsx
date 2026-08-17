import { useState } from "react";

import { api, ApiError } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";

type LoginProps = {
  onAuthenticated: () => void;
};

/**
 * Single-user gate (ADR-0015). There is no username: the lab has one operator,
 * and the password is set from the server with `make set-password`.
 */
export function Login({ onAuthenticated }: LoginProps) {
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
      setError(
        exc instanceof ApiError ? exc.message : "Could not reach the API."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-shell">
      <form className="panel login-panel" onSubmit={submit}>
        <div className="panel-head">
          <h2>Agentic Harness Lab</h2>
        </div>
        <p className="muted">Sign in to manage runs, models and skills.</p>
        {error !== null ? <ErrorBanner message={error} /> : null}
        <label>
          <span>Password</span>
          <input
            autoFocus
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>
        <button className="primary" disabled={busy || password === ""} type="submit">
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted">
          Lost the password? Run <code>make set-password</code> on the server.
        </p>
      </form>
    </main>
  );
}
