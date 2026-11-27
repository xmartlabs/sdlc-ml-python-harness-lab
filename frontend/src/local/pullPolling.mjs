// Pure helpers behind *Get more models* (ADR-0101), kept apart from the React
// component so `node --test` can cover them without a browser.

/** Wait before polling a download again after `failures` consecutive errors. */
export function pollDelay(failures, baseMs = 1500, maxMs = 30000) {
  if (failures <= 0) return baseMs;
  return Math.min(maxMs, baseMs * 2 ** failures);
}

/**
 * What to do with one poll answer.
 *
 * `outcome` is `{ job }` on success or `{ status }` (HTTP status, 0 = network)
 * on failure. Returns `{ action, failures }` where `action` is one of:
 * `"continue"` (poll again), `"done"` (completed: refresh the model list),
 * `"stop"` (failed: show the error), `"forget"` (the API no longer knows the
 * job: clear it), `"retry"` (transient error: poll again after backoff),
 * `"give_up"` (too many errors in a row).
 */
export function nextPollStep(outcome, failures, maxFailures = 8) {
  if (outcome.job) {
    if (outcome.job.status === "downloading") return { action: "continue", failures: 0 };
    if (outcome.job.status === "completed") return { action: "done", failures: 0 };
    return { action: "stop", failures: 0 };
  }
  if (outcome.status === 404) return { action: "forget", failures };
  const next = failures + 1;
  return { action: next >= maxFailures ? "give_up" : "retry", failures: next };
}

/**
 * Hands out request numbers so only the newest answer is applied: a slow
 * Hugging Face search must not replace a list asked for after it.
 */
export function createLatest() {
  let current = 0;
  return {
    next() {
      current += 1;
      return current;
    },
    isLatest(ticket) {
      return ticket === current;
    },
    cancel() {
      current += 1;
    }
  };
}

/** "0.24.0" -> [0, 24, 0]; "v0.30.0-rc1" -> [0, 30, 0]; "" -> []. */
export function versionParts(version) {
  const parts = [];
  for (const piece of String(version ?? "").trim().replace(/^v/, "").split(".")) {
    const match = /^\d+/.exec(piece);
    if (!match) break;
    parts.push(Number(match[0]));
  }
  return parts;
}

/**
 * True only when both versions are known and `version` is older than `min`:
 * an unknown server version never blocks or warns.
 */
export function olderThan(version, min) {
  const have = versionParts(version);
  const need = versionParts(min);
  if (have.length === 0 || need.length === 0) return false;
  for (let i = 0; i < Math.max(have.length, need.length); i += 1) {
    const a = have[i] ?? 0;
    const b = need[i] ?? 0;
    if (a !== b) return a < b;
  }
  return false;
}
