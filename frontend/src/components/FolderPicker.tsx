/**
 * The folder browser the audit opens on its first step (ADR-0045).
 *
 * It is the only place in the app where somebody navigates a filesystem, and
 * the first version read as a form: a right-aligned "Up one level" button, the
 * absolute path printed beside it until it ran off the panel, and rows that
 * said "open it to use it" without ever showing the thing you would press.
 *
 * So the three questions a file browser has to answer at a glance are answered
 * separately here:
 *
 * - *Where am I?* — a breadcrumb whose every ancestor is a jump, wrapping
 *   instead of overflowing, plus an explicit way back up one level.
 * - *What is in front of me?* — one row per folder, with a git repository drawn
 *   differently from a plain folder in icon, badge and wording rather than in
 *   colour alone.
 * - *What do I press?* — the action sits on the repository row itself, so
 *   choosing one is a single click, and again on the folder you are standing in
 *   when that folder is a repository.
 */

import type { LocalSourceListing } from "../types";
import { EmptyState } from "./EmptyState";
import { IconChevronRight, IconFolder, IconGitBranch, IconLevelUp } from "./icons";

type FolderPickerProps = {
  listing: LocalSourceListing;
  /** Something is running elsewhere on the page: nothing here is clickable. */
  disabled: boolean;
  /** This picker is fetching a directory. */
  browsing: boolean;
  /** The action this picker triggers is running. */
  working: boolean;
  onBrowse: (path: string) => void;
  onUse: (path: string) => void;
  /** What pressing the action on a repository does, e.g. "Audit this folder". */
  useLabel: string;
  /** The same button while it runs. */
  workingLabel: string;
  /** One line under the action, saying what it will do. */
  useHint: string;
};

type Crumb = { label: string; path: string };

/** The trail from the shared root down to where we are, each step a jump. */
function trail(listing: LocalSourceListing): Crumb[] {
  const rootName = (listing.root ?? "").split("/").filter(Boolean).pop() ?? "/";
  const segments = (listing.path ?? "").split("/").filter(Boolean);
  return [
    { label: rootName, path: "" },
    ...segments.map((name, index) => ({
      label: name,
      path: segments.slice(0, index + 1).join("/")
    }))
  ];
}

export function FolderPicker({
  listing,
  disabled,
  browsing,
  working,
  onBrowse,
  onUse,
  useLabel,
  workingLabel,
  useHint
}: FolderPickerProps) {
  const crumbs = trail(listing);
  const here = crumbs[crumbs.length - 1];
  const parent = listing.parent;
  const repositories = listing.entries.filter((entry) => entry.is_repo).length;

  return (
    <section aria-busy={browsing} className="folder-picker">
      <p className="folder-picker__how">
        <strong>Click a folder to open it.</strong> The ones marked{" "}
        <span className="folder-picker__inline-tag">
          <IconGitBranch size={12} /> git
        </span>{" "}
        are repositories you can audit — press <strong>{useLabel}</strong> on one, or
        open it first if you want to look inside.
      </p>

      <div className="folder-picker__bar">
        {parent == null ? (
          <span className="folder-picker__top">Top of the shared folder</span>
        ) : (
          <button
            className="ghost folder-picker__up"
            disabled={disabled}
            onClick={() => onBrowse(parent)}
            type="button"
          >
            <IconLevelUp size={15} />
            Up one level
          </button>
        )}
        <nav aria-label="Folder you are browsing" className="folder-picker__crumbs">
          {crumbs.map((crumb, index) =>
            index === crumbs.length - 1 ? (
              <span aria-current="location" className="crumb crumb--here" key={crumb.path}>
                {crumb.label}
              </span>
            ) : (
              <span className="crumb-step" key={crumb.path}>
                <button
                  className="crumb crumb--link"
                  disabled={disabled}
                  onClick={() => onBrowse(crumb.path)}
                  type="button"
                >
                  {crumb.label}
                </button>
                <IconChevronRight className="crumb-sep" size={13} />
              </span>
            )
          )}
        </nav>
      </div>

      {listing.is_repo ? (
        <div className="folder-picker__here folder-picker__here--repo">
          <div className="folder-picker__here-text">
            <strong>
              <IconGitBranch size={16} />
              {here.label}
              <span className="badge pick-tag">git</span>
            </strong>
            <span className="muted small">
              You are inside this repository
              {listing.branch ? ` on ${listing.branch}` : ""}. {useHint}
            </span>
          </div>
          <button
            className="primary"
            disabled={disabled}
            onClick={() => onUse(listing.path ?? "")}
            type="button"
          >
            {working ? workingLabel : useLabel}
          </button>
        </div>
      ) : (
        <p className="folder-picker__here-note muted small">
          <IconFolder size={14} />
          <span>
            <strong>{here.label}</strong> is a plain folder, not a git repository. Open
            one of the folders below to find the repository you want.
          </span>
        </p>
      )}

      {listing.entries.length === 0 ? (
        <EmptyState
          description={
            parent == null
              ? "This folder holds no subdirectories. Point HARNESS_LAB_LOCAL_SOURCES_DIR at the directory where your repositories live and restart the lab."
              : "This folder holds no subdirectories. Go back up and try another one."
          }
          title="Nothing to open here."
        />
      ) : (
        <ul className="folder-picker__list">
          {listing.entries.map((entry) => (
            <li
              className={`folder-row ${entry.is_repo ? "folder-row--repo" : ""}`}
              key={entry.path}
            >
              <button
                className="folder-row__open"
                disabled={disabled}
                onClick={() => onBrowse(entry.path)}
                type="button"
              >
                <span className="folder-row__icon">
                  {entry.is_repo ? <IconGitBranch size={16} /> : <IconFolder size={16} />}
                </span>
                <span className="folder-row__text">
                  <strong>
                    {entry.name}
                    {entry.is_repo ? <span className="badge pick-tag">git</span> : null}
                  </strong>
                  <span className="muted">
                    {entry.is_repo ? "Git repository — open to look inside" : "Folder — open it"}
                  </span>
                </span>
                <IconChevronRight className="folder-row__go" size={16} />
              </button>
              {entry.is_repo ? (
                <button
                  className="primary folder-row__use"
                  disabled={disabled}
                  onClick={() => onUse(entry.path)}
                  type="button"
                >
                  {working ? workingLabel : useLabel}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <p className="folder-picker__root muted small">
        Browsing <code>{listing.root}</code>
        {repositories > 0
          ? ` — ${repositories} git ${repositories === 1 ? "repository" : "repositories"} here.`
          : "."}{" "}
        Your repositories somewhere else? Set{" "}
        <code>HARNESS_LAB_LOCAL_SOURCES_DIR</code> in <code>.env</code> to the directory
        that holds them and restart the lab — one directory of repositories, never your
        home folder.
      </p>
    </section>
  );
}
