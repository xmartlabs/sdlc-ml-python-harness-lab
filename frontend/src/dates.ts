// Timestamp helpers shared by the run list and the improvements table.
//
// The API stores datetimes as naive UTC (SQLite drops tzinfo on persist), so it
// serializes them without an offset — "2026-07-27T11:25:46.466270". JavaScript
// reads an offset-less date-time as *local* time, which would shift every
// timestamp by the viewer's UTC offset, so we append the missing "Z" ourselves.
export function parseApiDate(iso: string | null | undefined): Date | null {
  if (!iso) {
    return null;
  }
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const date = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

// "2026-07-27 08:25" in the viewer's local timezone.
export function formatRunDate(iso: string | null | undefined): string {
  const date = parseApiDate(iso);
  if (!date) {
    return "—";
  }
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Compact age for dense cells: "just now", "12m", "5h", "3d", "6w". The exact
// timestamp always stays available in the cell's tooltip.
export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  const date = parseApiDate(iso);
  if (!date) {
    return "—";
  }
  const seconds = Math.max(0, Math.round((now - date.getTime()) / 1000));
  if (seconds < 60) {
    return "just now";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  const days = Math.round(hours / 24);
  if (days < 7) {
    return `${days}d ago`;
  }
  const weeks = Math.round(days / 7);
  return weeks < 5 ? `${weeks}w ago` : formatRunDate(iso);
}

// Elapsed wall-clock between two API timestamps ("12m 47s"), or "" when either
// end is missing (a run that never started, or is still running).
export function formatDuration(
  startIso: string | null | undefined,
  endIso: string | null | undefined
): string {
  const start = parseApiDate(startIso);
  const end = parseApiDate(endIso);
  if (!start || !end) {
    return "";
  }
  const seconds = Math.max(0, Math.round((end.getTime() - start.getTime()) / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
