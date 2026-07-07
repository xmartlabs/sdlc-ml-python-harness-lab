export function Settings() {
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
    </section>
  );
}
