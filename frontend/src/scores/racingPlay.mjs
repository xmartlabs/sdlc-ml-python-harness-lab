/**
 * racing-play-v1: a one-line summary of a run's racing scores (ADR-0102).
 * Returns null for any run without a `racing_play_mode` score.
 */
const LABELS = { played: 'Played', scripted: 'Scripted', unknown: 'Unknown' };

export function racingPlaySummary(scores) {
  const by = Object.fromEntries(scores.map((s) => [s.name, s]));
  const mode = by.racing_play_mode;
  if (!mode) return null;
  const label = mode.value_text ?? 'unknown';
  const source = (mode.reason ?? '').split('·')[1]?.trim().split(' ')[0];
  const badge = label !== 'unknown' && source ? `${LABELS[label] ?? label} · ${source}` : LABELS[label] ?? label;
  const finish = by.racing_finish_seconds?.value_num;
  const progress = by.racing_progress?.value_num;
  let outcome = 'No valid episode';
  if (finish != null) outcome = `Lap ${finish.toFixed(2)} s`;
  else if (progress != null) outcome = `No lap · ${(progress * 100).toFixed(1)}% of the lap`;
  const collisions = by.racing_collisions?.value_num;
  const resets = by.racing_resets?.value_num;
  const impacts = collisions != null ? `${collisions} collisions · ${resets ?? 0} resets` : '';
  const complete = by.racing_episode_complete ? by.racing_episode_complete.value_bool !== false : true;
  return { mode: label, badge, outcome, impacts, complete };
}
