import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { defineMessages, useMessages } from "../i18n";
import type { IconProps } from "./icons";
import { LanguageToggle } from "./LanguageToggle";
import { XmartlabsLogo } from "./XmartlabsLogo";
import {
  IconBook,
  IconChevronLeft,
  IconChevronRight,
  IconCompare,
  IconCpu,
  IconFiles,
  IconFlask,
  IconHome,
  IconLayers,
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

const MESSAGES = defineMessages({
  en: {
    groups: {
      evaluate: "Evaluate",
      evidence: "Evidence",
      local: "Local models",
      advanced: "Advanced",
      system: "System"
    },
    nav: {
      home: "Home",
      newEvaluation: "New evaluation",
      subject: "Validate your tool",
      audit: "Audit a repository",
      harnessByModel: "Harness x model",
      experiments: "Experiments",
      findings: "Findings",
      cases: "Cases",
      repositories: "Repositories",
      skills: "Skills",
      tuneLocal: "Tune your local model",
      localResults: "Results",
      runs: "Runs",
      compare: "Compare",
      benchmarks: "Benchmarks",
      improvements: "Improvements",
      health: "Health",
      guide: "Guide",
      settings: "Settings"
    },
    expandSidebar: "Expand sidebar",
    collapseSidebar: "Collapse sidebar",
    signOut: "Sign out",
    authOff: "Authentication is off — anyone who can reach this address can drive the lab. Run",
    authOffTail: "on the server to protect it."
  },
  es: {
    groups: {
      evaluate: "Evaluar",
      evidence: "Evidencia",
      local: "Modelos locales",
      advanced: "Avanzado",
      system: "Sistema"
    },
    nav: {
      home: "Inicio",
      newEvaluation: "Nueva evaluación",
      subject: "Validá tu herramienta",
      audit: "Auditar un repositorio",
      harnessByModel: "Arnés x modelo",
      experiments: "Experimentos",
      findings: "Hallazgos",
      cases: "Casos",
      repositories: "Repositorios",
      skills: "Skills",
      tuneLocal: "Optimizá tu modelo local",
      localResults: "Resultados",
      runs: "Corridas",
      compare: "Comparar",
      benchmarks: "Benchmarks",
      improvements: "Mejoras",
      health: "Estado",
      guide: "Guía",
      settings: "Configuración"
    },
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
};

type NavGroup = { title: GroupKey | null; items: NavItem[] };

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
    title: null,
    items: [{ to: "/", label: "home", icon: IconHome, end: true }]
  },
  {
    title: "evaluate",
    items: [
      { to: "/evaluate/new", label: "newEvaluation", icon: IconPlus },
      { to: "/evaluate/subject", label: "subject", icon: IconPuzzle },
      { to: "/evaluate/audit", label: "audit", icon: IconSearch },
      { to: "/evaluate/harness-by-model", label: "harnessByModel", icon: IconLayers },
      { to: "/evaluate/experiments", label: "experiments", icon: IconFlask }
    ]
  },
  {
    title: "evidence",
    items: [
      { to: "/evidence/findings", label: "findings", icon: IconSearch },
      { to: "/evidence/cases", label: "cases", icon: IconFiles },
      { to: "/evidence/repositories", label: "repositories", icon: IconRepo },
      { to: "/evidence/skills", label: "skills", icon: IconPuzzle }
    ]
  },
  {
    title: "local",
    items: [
      { to: "/local/tune", label: "tuneLocal", icon: IconCpu },
      { to: "/local/results", label: "localResults", icon: IconTrophy }
    ]
  },
  {
    title: "advanced",
    items: [
      { to: "/advanced/runs", label: "runs", icon: IconList },
      { to: "/advanced/compare", label: "compare", icon: IconCompare },
      { to: "/advanced/benchmarks", label: "benchmarks", icon: IconTrophy },
      { to: "/advanced/improvements", label: "improvements", icon: IconLightbulb }
    ]
  },
  {
    title: "system",
    items: [
      { to: "/system/health", label: "health", icon: IconPulse },
      { to: "/system/guide", label: "guide", icon: IconBook },
      { to: "/system/settings", label: "settings", icon: IconSettings }
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
                      isActive ? "nav-item active" : "nav-item"
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
        {children}
      </main>
    </div>
  );
}
