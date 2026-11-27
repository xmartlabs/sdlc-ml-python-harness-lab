/** The same deterministic rules run in the browser and the headless episode bridge.
 * Coordinates are metres; heading zero points along +x, positive toward +z.
 * Neither wall time nor rendering participates in the simulation.
 */
export const TICK_RATE = 60;
export const ACTION_TICKS = 12;
export const MAX_TICKS = TICK_RATE * 120;
const COUNTDOWN_TICKS = 3 * TICK_RATE;
const DT = 1 / TICK_RATE;
const wrap = (angle) => Math.atan2(Math.sin(angle), Math.cos(angle));
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const points = Array.from({ length: 180 }, (_, i) => {
  const angle = i / 180 * Math.PI * 2;
  const radius = 1 + 0.12 * Math.cos(3 * angle);
  return { x: 53 * Math.cos(angle) * radius, z: 37 * Math.sin(angle) * radius };
});
let length = 0;
const segments = points.map((p, i) => {
  const q = points[(i + 1) % points.length];
  const dx = q.x - p.x, dz = q.z - p.z, size = Math.hypot(dx, dz);
  const segment = { ...p, dx, dz, size, distance: length, heading: Math.atan2(dz, dx) };
  length += size;
  return segment;
});
function atDistance(distance) {
  const d = ((distance % length) + length) % length;
  const s = segments.find((segment) => segment.distance + segment.size >= d) ?? segments[0];
  const fraction = (d - s.distance) / s.size;
  return { x: s.x + s.dx * fraction, z: s.z + s.dz * fraction, heading: s.heading, distance: d };
}
export const TRACK = Object.freeze({
  id: 'tidal-ring', name: 'Tidal Ring', version: '1', width: 12, length,
  centerline: Object.freeze(points.map(Object.freeze)),
  checkpoints: Object.freeze(Array.from({ length: 12 }, (_, i) => Object.freeze({ ...atDistance((i + 1) / 12 * length), distance: (i + 1) / 12 * length }))),
});
function nearest(x, z) {
  let best;
  for (const segment of segments) {
    const t = clamp(((x - segment.x) * segment.dx + (z - segment.z) * segment.dz) / segment.size ** 2, 0, 1);
    const px = segment.x + segment.dx * t, pz = segment.z + segment.dz * t;
    const distanceSquared = (x - px) ** 2 + (z - pz) ** 2;
    if (!best || distanceSquared < best.distanceSquared) {
      best = { x: px, z: pz, distanceSquared, distance: segment.distance + t * segment.size,
        heading: segment.heading, lateral: ((z - pz) * segment.dx - (x - px) * segment.dz) / segment.size };
    }
  }
  return best;
}
export function createRace(seed = 0) {
  if (!Number.isSafeInteger(seed) || seed < 0) throw new Error('seed must be a non-negative safe integer');
  const start = atDistance(0);
  return { version: '1', trackId: TRACK.id, seed, tick: 0, phase: 'countdown',
    x: start.x, z: start.z, heading: start.heading, speed: 0, checkpointsPassed: 0,
    previousDistance: 0, progressDistance: 0, maxProgressDistance: 0,
    collisions: 0, offTrackTicks: 0, resets: 0, resetHeld: false, collisionHeld: false, finishTick: null };
}
export function isTerminal(state) { return state.phase === 'finished' || state.phase === 'timeout'; }
function validateControls(controls) {
  for (const key of Object.keys(controls)) {
    if (!['throttle','brake','steer','reset'].includes(key)) throw new Error('unknown controls field');
  }
  for (const key of ['throttle','brake','steer','reset']) {
    const value = controls[key] ?? 0;
    if (!Number.isFinite(value) || value < (key === 'steer' ? -1 : 0) || value > 1) throw new Error('controls must be finite and bounded');
  }
}
export function stepRace(state, controls = {}, ticks = 1) {
  validateControls(controls);
  if (!Number.isSafeInteger(ticks) || ticks < 0 || ticks > MAX_TICKS) throw new Error('ticks must be a bounded non-negative integer');
  if (isTerminal(state)) return [];
  const events = [];
  const throttle = controls.throttle ?? 0, brake = controls.brake ?? 0, steer = controls.steer ?? 0;
  const reset = (controls.reset ?? 0) >= 0.5;
  for (let i = 0; i < ticks; i++) {
    state.tick++;
    if (isTerminal(state)) continue;
    if (state.tick <= COUNTDOWN_TICKS) {
      if (state.tick === COUNTDOWN_TICKS) { state.phase = 'racing'; events.push('race_started'); }
      continue;
    }
    if (reset && !state.resetHeld) {
      const distance = state.checkpointsPassed / TRACK.checkpoints.length * length;
      const safe = atDistance(distance);
      state.x = safe.x; state.z = safe.z; state.heading = safe.heading; state.speed = 0;
      state.previousDistance = safe.distance; state.progressDistance = distance;
      state.resets++; state.collisionHeld = false; events.push('reset');
    }
    state.resetHeld = reset;
    // A held reset stops the vehicle; elapsed race time still advances.
    if (!reset) {
      const road = nearest(state.x, state.z);
      const offRoad = Math.abs(road.lateral) > TRACK.width / 2;
      if (offRoad) state.offTrackTicks++;
      state.speed = clamp(state.speed + (throttle * 9 - brake * 18 - 0.8 - state.speed * 0.035 - (offRoad ? 10 : 0)) * DT, 0, 21);
      state.heading = wrap(state.heading + steer * (state.speed / 9) * DT);
      state.x += Math.cos(state.heading) * state.speed * DT;
      state.z += Math.sin(state.heading) * state.speed * DT;
      const next = nearest(state.x, state.z);
      const barrier = Math.abs(next.lateral) > TRACK.width / 2 + 2;
      if (barrier) {
        if (!state.collisionHeld) { state.collisions++; events.push('collision'); }
        const side = Math.sign(next.lateral);
        state.x = next.x - Math.sin(next.heading) * side * (TRACK.width / 2 + 1.9);
        state.z = next.z + Math.cos(next.heading) * side * (TRACK.width / 2 + 1.9);
        state.speed *= 0.65;
      }
      // Hysteresis makes a sustained scrape one impact, until back on the road.
      if (barrier) state.collisionHeld = true;
      else if (Math.abs(next.lateral) < TRACK.width / 2) state.collisionHeld = false;
      let delta = next.distance - state.previousDistance;
      if (delta < -length / 2) delta += length;
      if (delta > length / 2) delta -= length;
      if (Math.abs(delta) < 1 && Math.abs(next.lateral) <= TRACK.width / 2) {
        state.progressDistance += delta;
        const target = (state.checkpointsPassed + 1) / TRACK.checkpoints.length * length;
        const targetOnTrack = target % length;
        const gap = Math.abs(next.distance - targetOnTrack);
        const nearGate = Math.min(gap, length - gap) < 1;
        if (state.progressDistance >= target && nearGate && delta > 0 && Math.abs(wrap(state.heading-next.heading)) < Math.PI / 2) {
          state.checkpointsPassed++; events.push('checkpoint');
          if (state.checkpointsPassed === TRACK.checkpoints.length) {
            state.phase = 'finished'; state.finishTick = state.tick; state.speed = 0; events.push('finished');
          }
        }
        state.maxProgressDistance = Math.max(state.maxProgressDistance, Math.min(state.progressDistance, length));
      }
      state.previousDistance = next.distance;
    }
    if (!isTerminal(state) && state.tick >= MAX_TICKS) { state.phase = 'timeout'; events.push('timeout'); }
  }
  return events;
}
export function raceMetrics(state) {
  const metrics = { finished: state.phase === 'finished' ? 1 : 0, progress: state.phase === 'finished' ? 1 : Math.min(state.maxProgressDistance / length, 0.999999),
    checkpoints_passed: state.checkpointsPassed, collisions: state.collisions,
    off_track_seconds: state.offTrackTicks / TICK_RATE, resets: state.resets,
    race_time_seconds: Math.max(0, ((state.finishTick ?? state.tick) - COUNTDOWN_TICKS) / TICK_RATE) };
  if (state.finishTick !== null) metrics.finish_time_seconds = (state.finishTick - COUNTDOWN_TICKS) / TICK_RATE;
  return metrics;
}
export function observeRace(state) {
  const road = nearest(state.x, state.z);
  const ahead = atDistance(road.distance + Math.max(7, state.speed * 0.7));
  return { track_id: TRACK.id, track_version: TRACK.version, seed: state.seed, tick: state.tick,
    phase: state.phase, countdown_seconds: Math.max(0, (COUNTDOWN_TICKS - state.tick) / TICK_RATE),
    game_time_seconds: Math.max(0, ((state.finishTick ?? state.tick) - COUNTDOWN_TICKS) / TICK_RATE),
    x: state.x, z: state.z, heading: state.heading, speed: state.speed,
    heading_error: wrap(Math.atan2(ahead.z - state.z, ahead.x - state.x) - state.heading),
    lateral_offset: road.lateral, lookahead: { x: ahead.x, z: ahead.z },
    upcoming_turn: wrap(atDistance(road.distance + 18).heading - road.heading),
    road_half_width: TRACK.width / 2, checkpoints_passed: state.checkpointsPassed,
    checkpoints_total: TRACK.checkpoints.length, progress: raceMetrics(state).progress,
    metrics: raceMetrics(state) };
}
/** Calibration controller, deliberately excluded from observations. */
export function baselineControls(state) {
  const observation = observeRace(state);
  return { throttle: state.speed < 13 ? 1 : 0.2, brake: state.speed > 14 ? 0.4 : 0,
    steer: clamp(observation.heading_error * 1.9, -1, 1), reset: 0 };
}
