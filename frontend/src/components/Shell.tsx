import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { defineMessages, useMessages } from "../i18n";
import type { IconProps } from "./icons";
import { LanguageToggle } from "./LanguageToggle";
import { XmartlabsLogo } from "./XmartlabsLogo";
import {
  IconChevronLeft,
  IconChevronRight,
  IconCpu,
  IconFiles,
  IconFlask,
  IconHome,
  IconList,
  IconPuzzle,
  IconRepo,
  IconSettings,
  IconTrendUp
} from "./icons";

type ShellProps = {
  children: ReactNode;
  /** False in open mode: no password is set, so the UI warns instead of offering sign-out. */
  authEnabled: boolean;
  onLogout: () => void;
  /** Runs queued or running, from the list App already polls. */
  activeRuns?: number;
};

const COLLAPSED_STORAGE_KEY = "ahl.sidebar.collapsed";

const MESSAGES = defineMessages({
  en: {
    groups: {
      tryOut: "Try",
      lab: "Lab"
    },
    nav: {
      home: "Home",
      models: "Models and harness",
      skills: "Skills",
      benchmarks: "Benchmarks",
      repo: "Your repo",
      results: "Results",
      metrics: "Metrics",
      runs: "Runs",
      system: "System"
    },
    runsActive: (n: number) => `Runs · ${n} active`,
    runsNone: "Runs · none active",
    expandSidebar: "Expand sidebar",
    collapseSidebar: "Collapse sidebar",
    signOut: "Sign out",
    authOff: "Authentication is off — anyone who can reach this address can drive the lab. Run",
    authOffTail: "on the server to protect it."
  },
  es: {
    groups: {
      tryOut: "Probar",
      lab: "Lab"
    },
    nav: {
      home: "Inicio",
      models: "Modelos y arnés",
      skills: "Skills",
      benchmarks: "Benchmarks",
      repo: "Tu repo",
      results: "Resultados",
      metrics: "Métricas",
      runs: "Corridas",
      system: "Sistema"
    },
    runsActive: (n: number) => `Corridas · ${n} activas`,
    runsNone: "Corridas · ninguna activa",
    expandSidebar: "Expandir la barra lateral",
    collapseSidebar: "Contraer la barra lateral",
    signOut: "Cerrar sesión",
    authOff:
      "La autenticación está desactivada: cualquiera que llegue a esta dirección puede usar el lab. Corré",
    authOffTail: "en el servidor para protegerlo."
  }
});

type NavKey = keyof (typeof MESSAGES)["en"]["nav"];
type GroupKey = keyof (typeof MESSAGES)["en"]["groups"];

type NavItem = {
  to: string;
  label: NavKey;
  icon: ComponentType<IconProps>;
  end?: boolean;
  /** Screens that belong to this entry: it stays highlighted inside them. */
  owns?: string[];
};

type NavGroup = { title: GroupKey | null; items: NavItem[] };

/**
 * Navigation by capability (ADR-0090): seven entries instead of twenty.
 *
 * Each entry opens a hub — a board of cards saying what the capability does,
 * whether it is built, and where it lives. Every screen the old navigation
 * listed still exists at the same route; it is now reached from its hub, and
 * the hub's entry stays highlighted while you are inside it (`owns`).
 *
 * The routes the backend emits (attention feed) are untouched, which a contract
 * test checks (tests/harness_lab_app/test_attention.py).
 */
const NAV_GROUPS: NavGroup[] = [
  {
    title: null,
    items: [{ to: "/", label: "home", icon: IconHome, end: true }]
  },
  {
    // Each entry opens its guided flow (ADR-0093); the screens it drives keep
    // their routes and highlight the entry while you are inside them.
    title: "tryOut",
    items: [
      {
        // Models and harness are one flow since 2026-11-20 (ADR-0093).
        to: "/flows/models",
        label: "models",
        icon: IconCpu,
        owns: [
          "/models",
          "/local/",
          "/evaluate/compare-models",
          "/harness",
          "/evaluate/harness-by-model",
          "/evaluate/new"
        ]
      },
      {
        // Trying and improving a skill are one flow too.
        to: "/flows/skills",
        label: "skills",
        icon: IconPuzzle,
        owns: ["/evidence/skills"]
      },
      {
        to: "/benchmarks",
        label: "benchmarks",
        icon: IconFiles,
        owns: ["/evidence/cases"]
      },
      {
        to: "/your-repo",
        label: "repo",
        icon: IconRepo,
        owns: ["/evaluate/audit", "/evaluate/subject", "/evidence/repositories"]
      }
    ]
  },
  {
    title: "lab",
    items: [
      { to: "/flows/metrics", label: "metrics", icon: IconTrendUp, owns: ["/advanced/compare", "/advanced/improvements", "/evidence/findings"] },
      { to: "/advanced/runs", label: "runs", icon: IconList, owns: ["/advanced/runs"] },
      {
        to: "/results",
        label: "results",
        icon: IconFlask,
        owns: ["/evaluate/experiments", "/advanced/benchmarks"]
      },
      { to: "/system", label: "system", icon: IconSettings, owns: ["/system/"] }
    ]
  }
];

function ownsPath(item: NavItem, pathname: string): boolean {
  return (item.owns ?? []).some((prefix) =>
    prefix.endsWith("/") ? pathname.startsWith(prefix) : pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
}

function getStoredCollapsed(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
}

export function Shell({ children, authEnabled, onLogout, activeRuns = 0 }: ShellProps) {
  const [collapsed, setCollapsed] = useState(getStoredCollapsed);
  const { pathname } = useLocation();
  const m = useMessages(MESSAGES);

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
              <div className="brand-heading">
                <div className="brand-logo-container">
                  <XmartlabsLogo width={120} />
                </div>
                <div className="brand-meta">
                  <span className="brand-kicker">AI Lab Platform</span>
                  <strong className="brand-title">Harness Lab</strong>
                </div>
              </div>
            )}
            <button
              aria-expanded={!collapsed}
              aria-label={collapsed ? m.expandSidebar : m.collapseSidebar}
              className="sidebar-toggle"
              onClick={() => setCollapsed((current) => !current)}
              type="button"
            >
              {collapsed ? (
                <div className="collapsed-brand-toggle">
                  <XmartlabsLogo height={20} variant="icon-only" width={20} />
                  <span className="collapsed-arrow">
                    <IconChevronRight size={14} />
                  </span>
                </div>
              ) : (
                <IconChevronLeft size={16} />
              )}
            </button>
          </div>
          {/* The language switch sits with the brand, where it is seen first. */}
          <div className="brand-lang">
            <LanguageToggle compact={collapsed} />
          </div>
        </div>
        <nav className="nav">
          {NAV_GROUPS.map((group) => (
            <div className="nav-group" key={group.title ?? "root"}>
              {group.title && !collapsed ? (
                <p className="nav-group-title">{m.groups[group.title]}</p>
              ) : null}
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    className={({ isActive }) =>
                      isActive || ownsPath(item, pathname) ? "nav-item active" : "nav-item"
                    }
                    end={item.end}
                    key={item.to}
                    title={m.nav[item.label]}
                    to={item.to}
                  >
                    <span className="nav-item-icon">
                      <Icon size={16} />
                    </span>
                    {collapsed ? null : (
                      <span className="nav-item-label">{m.nav[item.label]}</span>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        {collapsed ? null : (
          <div className="sidebar-foot">
            <div className="sidebar-foot-brand">
              <span className="sidebar-foot-badge">XMARTLABS</span>
              <span className="sidebar-foot-edition">Agentic Harness Lab</span>
            </div>
            {authEnabled ? (
              <button className="button-link" onClick={onLogout} type="button">
                {m.signOut}
              </button>
            ) : null}
          </div>
        )}
      </aside>
      <main className="workbench">
        {authEnabled ? null : (
          <div className="auth-banner" role="status">
            {m.authOff} <code>make set-password</code> {m.authOffTail}
          </div>
        )}
        {/* Runs are one click away from every flow (ADR-0093). */}
        <div className="workbench-topbar">
          <NavLink
            className={activeRuns > 0 ? "runs-pill runs-pill--live" : "runs-pill"}
            to="/advanced/runs"
          >
            <span aria-hidden="true" className="runs-pill-dot" />
            {activeRuns > 0 ? m.runsActive(activeRuns) : m.runsNone}
          </NavLink>
        </div>
        {children}
      </main>
    </div>
  );
}
