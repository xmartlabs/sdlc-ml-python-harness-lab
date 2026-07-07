import type { ReactNode } from "react";

type PageId = "dashboard" | "runs" | "cases" | "compare" | "settings";

type ShellProps = {
  activePage: PageId;
  children: ReactNode;
  inspector: ReactNode;
  onNavigate: (page: PageId) => void;
};

const NAV_ITEMS: Array<{ id: PageId; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "runs", label: "Runs" },
  { id: "cases", label: "Cases" },
  { id: "compare", label: "Compare" },
  { id: "settings", label: "Settings" }
];

export function Shell({ activePage, children, inspector, onNavigate }: ShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-kicker">Local web app</span>
          <strong>Agentic Harness Lab</strong>
        </div>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <button
              aria-pressed={activePage === item.id}
              className={activePage === item.id ? "nav-item active" : "nav-item"}
              key={item.id}
              onClick={() => onNavigate(item.id)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <p className="sidebar-label">Mode</p>
          <p className="sidebar-value">Operational shell</p>
        </div>
      </aside>
      <main className="workbench">{children}</main>
      <aside className="inspector">{inspector}</aside>
    </div>
  );
}
