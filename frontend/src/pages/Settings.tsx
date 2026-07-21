import type { ThemeMode } from "../theme";

type SettingsProps = {
  theme: ThemeMode;
  onThemeChange: (theme: ThemeMode) => void;
};

export function Settings({ theme, onThemeChange }: SettingsProps) {
  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Settings</p>
          <h1>Local runtime</h1>
          <p className="lede">Configure the repo root, docker runtime, and future auth/tracking options.</p>
        </div>
      </header>

      <section className="panel">
        <div className="panel-head">
          <h2>Appearance</h2>
          <span className="muted">Persisted locally</span>
        </div>
        <div className="settings-list">
          <div className="theme-toggle-row">
            <span>Theme</span>
            <div className="theme-toggle" role="group">
              <button
                aria-pressed={theme === "light"}
                className={theme === "light" ? "active" : ""}
                onClick={() => onThemeChange("light")}
                type="button"
              >
                Light
              </button>
              <button
                aria-pressed={theme === "dark"}
                className={theme === "dark" ? "active" : ""}
                onClick={() => onThemeChange("dark")}
                type="button"
              >
                Dark
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Runtime defaults</h2>
          <span className="muted">Preview scaffold</span>
        </div>
        <div className="settings-list">
          <div>
            <span>Repository</span>
            <strong>Mounted into API and worker containers</strong>
          </div>
          <div>
            <span>Database</span>
            <strong>SQLite at data/app/harness_lab.db</strong>
          </div>
          <div>
            <span>Frontend</span>
            <strong>Vite on :5173 with /api proxy</strong>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Coming soon</h2>
          <span className="muted">Not yet wired</span>
        </div>
        <div className="empty-state">
          <strong>Auth and tracking settings.</strong>
          <p>Login/session controls and usage tracking preferences will land here in a future iteration.</p>
        </div>
      </section>
    </section>
  );
}
