import { useEffect, useRef } from "react";
import { Outlet, useParams } from "react-router-dom";

import { api } from "../api";
import { Shell } from "./Shell";
import type { RunRecord } from "../types";

// Only states that transition on their own are polled. `awaiting_manual_score`
// is a resting state (it changes only when the user scores, which updates the
// UI directly), so polling it would loop forever.
const POLLABLE_STATES: RunRecord["state"][] = [
  "pending",
  "running",
  "scoring",
  "auditing"
];
const POLL_INTERVAL_MS = 2000;

type LayoutProps = {
  runs: RunRecord[];
  onUpdateRun: (run: RunRecord) => void;
  authEnabled: boolean;
  onLogout: () => void;
};

export function Layout({ runs, onUpdateRun, authEnabled, onLogout }: LayoutProps) {
  const { runId } = useParams();
  // Keep the callback in a ref so it is not an effect dependency (it changes
  // every render and would otherwise restart the interval on each poll).
  const onUpdateRef = useRef(onUpdateRun);
  onUpdateRef.current = onUpdateRun;

  // Depend on the primitive state, not the whole runs array: the effect then
  // re-runs only when THIS run's state actually changes, not on every poll
  // response (which was causing a tight request loop).
  const trackedState = runId
    ? runs.find((run) => run.run_id === runId)?.state
    : undefined;

  useEffect(() => {
    if (!runId || !trackedState || !POLLABLE_STATES.includes(trackedState)) {
      return;
    }

    let active = true;
    const refresh = () => {
      api
        .getRun(runId)
        .then((updated) => {
          if (active) {
            onUpdateRef.current(updated);
          }
        })
        .catch(() => {
          // Keep last known state on transient API errors.
        });
    };

    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [runId, trackedState]);

  return (
    <Shell authEnabled={authEnabled} onLogout={onLogout}>
      <Outlet />
    </Shell>
  );
}
