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
