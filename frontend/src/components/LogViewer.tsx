import { useMemo, useState } from "react";
import type { RunEventRecord } from "../types";

type LogViewerProps = {
  events: RunEventRecord[];
};

type ParsedLine = {
  id: number;
  time: string | null;
  level: string;
  message: string;
  extras: string;
  raw: string;
};

const LEVELS = ["ALL", "INFO", "WARN", "ERROR"] as const;
type LevelFilter = (typeof LEVELS)[number];

// opencode --print-logs emits logfmt lines:
//   timestamp=2026-07-07T23:58:14.255Z level=INFO run=xx message="creating instance" directory=/app
const LOGFMT_RE = /^timestamp=(\S+)\s+level=(\S+)\s+(.*)$/;
const MESSAGE_RE = /message=("([^"]*)"|\S+)/;

/**
 * The wall-clock time of a log line, in the reader's own timezone.
 *
 * opencode stamps its lines in UTC with an explicit `Z`. Slicing the time out
 * of the string kept that UTC clock while looking exactly like a local one, so
 * every line read hours off — and the one question a log timestamp has to
 * answer, "has this run gone quiet?", could not be answered by eye.
 */
function localTime(timestamp: string): string {
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) {
    return timestamp.includes("T") ? timestamp.split("T")[1].replace("Z", "") : timestamp;
  }
  const hhmmss = parsed.toLocaleTimeString(undefined, { hour12: false });
  return `${hhmmss}.${String(parsed.getMilliseconds()).padStart(3, "0")}`;
}

function parseLine(event: RunEventRecord): ParsedLine {
  const match = LOGFMT_RE.exec(event.message.trim());
  if (!match) {
    return {
      id: event.id,
      time: null,
      level: (event.level || "info").toUpperCase(),
      message: event.message,
      extras: "",
      raw: event.message
    };
  }
  const [, timestamp, level, rest] = match;
  const messageMatch = MESSAGE_RE.exec(rest);
  const message = messageMatch ? (messageMatch[2] ?? messageMatch[1]) : "";
  const extras = messageMatch ? rest.replace(messageMatch[0], "").trim() : rest.trim();
  return {
    id: event.id,
    time: localTime(timestamp),
    level: level.toUpperCase(),
    message: message || rest,
    extras,
    raw: event.message
  };
}

function levelClass(level: string): string {
  if (level === "ERROR" || level === "FATAL") return "error";
  if (level === "WARN" || level === "WARNING") return "warn";
  if (level === "DEBUG") return "debug";
  return "info";
}

export function LogViewer({ events }: LogViewerProps) {
  const [filter, setFilter] = useState<LevelFilter>("ALL");
  const [showExtras, setShowExtras] = useState(false);

  const parsed = useMemo(() => events.map(parseLine), [events]);
  const counts = useMemo(() => {
    const byLevel: Record<string, number> = {};
    for (const line of parsed) {
      const key = line.level === "WARNING" ? "WARN" : line.level;
      byLevel[key] = (byLevel[key] ?? 0) + 1;
    }
    return byLevel;
  }, [parsed]);

  const visible = useMemo(() => {
    if (filter === "ALL") {
      return parsed;
    }
    return parsed.filter((line) => line.level === filter || (filter === "WARN" && line.level === "WARNING"));
  }, [parsed, filter]);

  if (events.length === 0) {
    return <p className="muted">No log events yet.</p>;
  }

  return (
    <div className="log-viewer">
      <div className="log-viewer-toolbar">
        {LEVELS.map((level) => (
          <button
            className={filter === level ? "badge neutral" : "badge"}
            key={level}
            onClick={() => setFilter(level)}
            type="button"
          >
            {level === "ALL" ? `All (${parsed.length})` : `${level} (${counts[level] ?? 0})`}
          </button>
        ))}
        <button
          className={showExtras ? "badge neutral" : "badge"}
          onClick={() => setShowExtras((current) => !current)}
          type="button"
        >
          {showExtras ? "Hide fields" : "Show fields"}
        </button>
      </div>
      <div className="run-console log-viewer-lines">
        {visible.length === 0 ? (
          <p className="muted">No {filter} lines.</p>
        ) : (
          visible.map((line) => (
            <div className={`run-console-line ${levelClass(line.level)}`} key={line.id}>
              <span className="run-console-time">{line.time ?? ""}</span>
              <span className={`log-level log-level-${levelClass(line.level)}`}>{line.level}</span>
              <span className="log-message">
                {line.message}
                {showExtras && line.extras ? <small className="log-extras"> {line.extras}</small> : null}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
