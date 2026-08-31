import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import type { IconProps } from "./icons";
import {
  IconBook,
  IconCompare,
  IconFiles,
  IconFlask,
  IconHome,
  IconLightbulb,
  IconList,
  IconPlus,
  IconPulse,
  IconPuzzle,
  IconRepo,
  IconSearch,
  IconSettings,
  IconTrophy
} from "./icons";

type ShellProps = {
  children: ReactNode;
  /** False in open mode: no password is set, so the UI warns instead of offering sign-out. */
  authEnabled: boolean;
  onLogout: () => void;
};

const COLLAPSED_STORAGE_KEY = "ahl.sidebar.collapsed";

type NavItem = {
  to: string;
  label: string;
  icon: ComponentType<IconProps>;
  end?: boolean;
};

type NavGroup = { title: string; items: NavItem[] };

/**
 * Navigation grouped by what a person came to do, not by which table backs it.
 *
 * "Evaluate" is where you go with a question, "Evidence" is what the lab knows,
 * "Advanced" is the operational machinery underneath. Nothing was removed in
 * the regrouping — Runs, Compare and the leaderboard all still exist, they just
 * stopped competing with the workflows for the top of the page.
 */
const NAV_GROUPS: NavGroup[] = [
  {
    title: "",
    items: [{ to: "/", label: "Home", icon: IconHome, end: true }]
  },
  {
    title: "Evaluate",
    items: [
      { to: "/evaluate/new", label: "New evaluation", icon: IconPlus },
      { to: "/evaluate/experiments", label: "Experiments", icon: IconFlask }
    ]
  },
  {
    title: "Evidence",
    items: [
      { to: "/evidence/findings", label: "Findings", icon: IconSearch },
      { to: "/evidence/cases", label: "Cases", icon: IconFiles },
      { to: "/evidence/repositories", label: "Repositories", icon: IconRepo },
      { to: "/evidence/skills", label: "Skills", icon: IconPuzzle }
    ]
  },
  {
    title: "Advanced",
    items: [
      { to: "/advanced/runs", label: "Runs", icon: IconList },
      { to: "/advanced/compare", label: "Compare", icon: IconCompare },
      { to: "/advanced/benchmarks", label: "Benchmarks", icon: IconTrophy },
      { to: "/advanced/improvements", label: "Improvements", icon: IconLightbulb }
    ]
  },
  {
    title: "System",
    items: [
      { to: "/system/health", label: "Health", icon: IconPulse },
      { to: "/system/guide", label: "Guide", icon: IconBook },
      { to: "/system/settings", label: "Settings", icon: IconSettings }
    ]
  }
];

function getStoredCollapsed(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
}

export function Shell({ children, authEnabled, onLogout }: ShellProps) {
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
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.title || "root"}>
              {group.title && !collapsed ? (
                <p className="nav-group-title">{group.title}</p>
              ) : null}
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    className={({ isActive }) =>
                      isActive ? "nav-item active" : "nav-item"
                    }
                    end={item.end}
                    key={item.to}
                    title={item.label}
                    to={item.to}
                  >
                    <span className="nav-item-icon">
                      <Icon size={16} />
                    </span>
                    {collapsed ? null : (
                      <span className="nav-item-label">{item.label}</span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        {collapsed ? null : (
          <div className="sidebar-foot">
            <p className="sidebar-label">Mode</p>
            <p className="sidebar-value">Operational shell</p>
            {authEnabled ? (
              <button className="button-link" onClick={onLogout} type="button">
                Sign out
              </button>
            ) : null}
          </div>
        )}
      </aside>
      <main className="workbench">
        {authEnabled ? null : (
          <div className="auth-banner" role="status">
            Authentication is off — anyone who can reach this address can drive the
            lab. Run <code>make set-password</code> on the server to protect it.
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
