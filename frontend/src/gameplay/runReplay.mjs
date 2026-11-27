/**
 * Replay a graded racing-play run tick by tick, for watching (ADR-0103).
 *
 * The grader replays `controls` under its condition in action-sized chunks
 * (`stepRace(state, c, ticks_per_action)`); the engine advances one tick at a
 * time inside that call, so stepping a tick at a time here reaches the same
 * state on every tick, and the lap shown is the lap that was scored.
 */
import { createRace, isTerminal, observeRace, stepRace } from './racing.mjs';

const ZERO = Object.freeze({ throttle: 0, brake: 0, steer: 0, reset: 0 });

/** A replay cursor over `controls` under the grader's `condition`. */
export function createRunReplay(condition, controls) {
  const countdown = condition.countdown_ticks;
  const perAction = condition.ticks_per_action;
  const horizon = condition.horizon_ticks;
  const lastTick = Math.min(horizon, countdown + controls.length * perAction);
  const state = createRace(condition.seed);
  return {
    state,
    /** The tick at which this replay stops. */
    lastTick,
    /** Whether there is nothing left to replay. */
    done() { return state.tick >= lastTick || isTerminal(state); },
    /** The action whose controls drive the next tick, or -1 in the countdown. */
    actionIndex() {
      return state.tick < countdown ? -1 : Math.floor((state.tick - countdown) / perAction);
    },
    /** Advance up to `ticks` ticks; returns the engine events they produced. */
    advance(ticks) {
      const events = [];
      for (let i = 0; i < ticks && !this.done(); i++) {
        const index = this.actionIndex();
        events.push(...stepRace(state, index < 0 ? ZERO : controls[index], 1));
      }
      return events;
    },
    observe() { return observeRace(state); },
  };
}
