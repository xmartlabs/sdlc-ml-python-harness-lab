import { useEffect } from "react";
import { Outlet, useParams } from "react-router-dom";

import { api } from "../api";
import { Shell } from "./Shell";
import type { RunRecord } from "../types";

const POLLABLE_STATES: RunRecord["state"][] = ["pending", "running", "awaiting_manual_score"];
const POLL_INTERVAL_MS = 1500;

type LayoutProps = {
  runs: RunRecord[];
  onUpdateRun: (run: RunRecord) => void;
};

export function Layout({ runs, onUpdateRun }: LayoutProps) {
  const { runId } = useParams();

  useEffect(() => {
    if (!runId) {
      return;
    }
    const tracked = runs.find((run) => run.run_id === runId);
    if (!tracked || !POLLABLE_STATES.includes(tracked.state)) {
      return;
    }

    let active = true;
    const refresh = () => {
      api
        .getRun(runId)
        .then((updated) => {
          if (active) {
            onUpdateRun(updated);
          }
        })
        .catch(() => {
          // Keep last known state on transient API errors.
        });
    };

    refresh();
    const timer = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [runs, runId, onUpdateRun]);

  return (
    <Shell>
      <Outlet />
    </Shell>
  );
}
