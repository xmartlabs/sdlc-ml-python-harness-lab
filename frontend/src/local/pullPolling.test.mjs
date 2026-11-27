import test from 'node:test';
import assert from 'node:assert/strict';
import { createLatest, nextPollStep, olderThan, pollDelay, versionParts } from './pullPolling.mjs';

test('a running download keeps polling and resets the error count', () => {
  assert.deepEqual(nextPollStep({ job: { status: 'downloading' } }, 3), { action: 'continue', failures: 0 });
});

test('completed refreshes, failed stops', () => {
  assert.equal(nextPollStep({ job: { status: 'completed' } }, 0).action, 'done');
  assert.equal(nextPollStep({ job: { status: 'failed' } }, 0).action, 'stop');
});

test('a proxy error mid-download is retried, not fatal', () => {
  const first = nextPollStep({ status: 502 }, 0);
  assert.deepEqual(first, { action: 'retry', failures: 1 });
  const network = nextPollStep({ status: 0 }, first.failures);
  assert.deepEqual(network, { action: 'retry', failures: 2 });
});

test('the page gives up after too many errors in a row', () => {
  assert.equal(nextPollStep({ status: 500 }, 7, 8).action, 'give_up');
});

test('a job the API forgot (restart, pruned) is cleared', () => {
  assert.equal(nextPollStep({ status: 404 }, 2).action, 'forget');
});

test('backoff doubles from the base and is capped', () => {
  assert.equal(pollDelay(0), 1500);
  assert.equal(pollDelay(1), 3000);
  assert.equal(pollDelay(2), 6000);
  assert.equal(pollDelay(10), 30000);
});

test('only the newest search answer applies', () => {
  const latest = createLatest();
  const slowSearch = latest.next();
  const curated = latest.next();
  assert.equal(latest.isLatest(curated), true);
  assert.equal(latest.isLatest(slowSearch), false);
  latest.cancel();
  assert.equal(latest.isLatest(curated), false);
});

test('versions parse with a leading v and a pre-release suffix', () => {
  assert.deepEqual(versionParts('0.24.0'), [0, 24, 0]);
  assert.deepEqual(versionParts('v0.30.0-rc1'), [0, 30, 0]);
  assert.deepEqual(versionParts(''), []);
});

test('an older Ollama is flagged, a newer or unknown one is not', () => {
  assert.equal(olderThan('0.24.0', '0.30.0'), true); // the Jetson that failed
  assert.equal(olderThan('0.30.0', '0.30.9'), true);
  assert.equal(olderThan('0.30.9', '0.30.9'), false);
  assert.equal(olderThan('0.40.18', '0.30.9'), false);
  assert.equal(olderThan('0.30', '0.30.0'), false);
  assert.equal(olderThan('', '0.30.0'), false); // unknown never warns
  assert.equal(olderThan('0.24.0', ''), false); // no floor
});
