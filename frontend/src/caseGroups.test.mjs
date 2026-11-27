import test from 'node:test';
import assert from 'node:assert/strict';
import { CASE_GROUPS, caseGroup, groupCases } from './caseGroups.mjs';

// Tags as the benchmarks' task.yaml declare them today.
const TAGS = {
  'cli-textstats': ['cli', 'single-file', 'stdlib', 'objective'],
  'security-fix-v1': ['python', 'stdlib', 'security', 'bugfix', 'graded', 'ships-tests'],
  'agent-containment-v1': ['python', 'stdlib', 'safety', 'containment', 'ops', 'graded', 'hidden-grader'],
  'pr-review-v1': ['python', 'stdlib', 'code-review', 'security', 'concurrency', 'graded', 'ships-tests', 'hidden-grader'],
  'security-audit-v1': ['python', 'fastapi', 'api', 'security', 'audit', 'graded', 'hidden-grader'],
  'chess3d-v1': ['godot', '3d', 'game', 'frontier', 'graded', 'ships-tests', 'own-image'],
  'racing-play-v1': ['play', 'racing', 'deterministic', 'graded', 'hidden-grader', 'node'],
  'django__django-11099': ['swe-bench', 'django/django'],
};
const row = (id) => ({ case_id: id, tags: TAGS[id] });

test('each kind of case lands on its own shelf', () => {
  assert.equal(caseGroup(row('cli-textstats')), 'code');
  assert.equal(caseGroup(row('django__django-11099')), 'code');
  assert.equal(caseGroup(row('pr-review-v1')), 'review');
  assert.equal(caseGroup(row('security-audit-v1')), 'review');
  assert.equal(caseGroup(row('chess3d-v1')), 'game_3d');
  assert.equal(caseGroup(row('racing-play-v1')), 'gameplay');
});

test('security work that changes code stays in code, not review', () => {
  assert.equal(caseGroup(row('security-fix-v1')), 'code');
  assert.equal(caseGroup(row('agent-containment-v1')), 'code');
});

test('a game played in 3D is gameplay: play wins over 3d', () => {
  assert.equal(caseGroup({ tags: ['play', '3d', 'godot'] }), 'gameplay');
});

test('a case without tags is code', () => {
  assert.equal(caseGroup({ case_id: 'x' }), 'code');
  assert.equal(caseGroup(undefined), 'code');
});

test('groups follow the fixed order, skip empty ones and keep input order inside', () => {
  const rows = ['racing-play-v1', 'security-fix-v1', 'chess3d-v1', 'cli-textstats'].map(row);
  const out = groupCases(rows);
  assert.deepEqual(out.map((g) => g.group), ['code', 'game_3d', 'gameplay']);
  assert.deepEqual(out[0].items.map((r) => r.case_id), ['security-fix-v1', 'cli-textstats']);
  assert.ok(out.every((g) => CASE_GROUPS.includes(g.group)));
});

test('no case is lost or duplicated', () => {
  const rows = Object.keys(TAGS).map(row);
  const flat = groupCases(rows).flatMap((g) => g.items);
  assert.equal(flat.length, rows.length);
  assert.deepEqual(new Set(flat), new Set(rows));
});
