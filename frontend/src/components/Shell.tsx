import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

type ShellProps = {
  children: ReactNode;
};

const COLLAPSED_STORAGE_KEY = "ahl.sidebar.collapsed";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: "🏠", end: true },
  { to: "/runs", label: "Runs", icon: "▶", end: false },
  { to: "/cases", label: "Cases", icon: "🗂", end: false },
  { to: "/skills", label: "Skills", icon: "🧩", end: false },
  { to: "/compare", label: "Compare", icon: "⇄", end: false },
  { to: "/settings", label: "Settings", icon: "⚙", end: false }
] as const;

function getStoredCollapsed(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
}

export function Shell({ children }: ShellProps) {
  const [collapsed, setCollapsed] = useState(getStoredCollapsed);

  useEffect(() => {
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, collapsed ? "true" : "false");
  }, [collapsed]);

  const shellClassName = ["app-shell", collapsed ? "app-shell--nav-collapsed" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={shellClassName}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-row">
            {collapsed ? null : (
              <div>
                <span className="brand-kicker">Local web app</span>
                <strong>Agentic Harness Lab</strong>
              </div>
            )}
            <button
              aria-expanded={!collapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="sidebar-toggle"
              onClick={() => setCollapsed((current) => !current)}
              type="button"
            >
              {collapsed ? "»" : "«"}
            </button>
          </div>
        </div>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}
              end={item.end}
              key={item.to}
              title={item.label}
              to={item.to}
            >
              <span aria-hidden="true" className="nav-item-icon">
                {item.icon}
              </span>
              {collapsed ? null : <span className="nav-item-label">{item.label}</span>}
            </NavLink>
          ))}
        </nav>
        {collapsed ? null : (
          <div className="sidebar-foot">
            <p className="sidebar-label">Mode</p>
            <p className="sidebar-value">Operational shell</p>
          </div>
        )}
      </aside>
      <main className="workbench">{children}</main>
    </div>
  );
}
