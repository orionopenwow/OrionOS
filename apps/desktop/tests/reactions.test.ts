// Pure tests for the black hole's smart-reaction reducer (Art stage 6).
// Run: node --test apps/desktop/tests/reactions.test.ts   (Node 22.18+ strips the types itself; no extra packages)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  IDLE_S,
  ORBIT_MAX,
  dayTone,
  initialReactions,
  isAnimating,
  reactionBodies,
  reactionSignals,
  reactionsReducer,
  type ReactionAction,
  type ReactionState,
} from '../src/lib/blackhole/reactions.ts';

const run = (s: ReactionState, ...actions: ReactionAction[]) => actions.reduce(reactionsReducer, s);
const ticks = (s: ReactionState, from: number, to: number, step = 0.1) => {
  for (let t = from; t <= to + 1e-9; t += step) s = reactionsReducer(s, { type: 'tick', now: t });
  return s;
};
const bodyCount = (b: Float32Array) => {
  let n = 0;
  for (let i = 2; i < b.length; i += 4) if (b[i] > 0) n++;
  return n;
};

test('day tone: night, dawn, day', () => {
  assert.equal(dayTone(2).night, 1);
  assert.equal(dayTone(13).night, 0);
  assert.equal(dayTone(13).dawn, 0);
  assert.equal(dayTone(6.5).dawn, 1);
  assert.ok(dayTone(21).night > 0 && dayTone(21).night < 1);
  assert.equal(dayTone(26).night, dayTone(2).night); // wraps
});

test('signals ease smoothly instead of jumping', () => {
  let s = initialReactions(0, 13);
  s = run(s, { type: 'signals', msg: { thinking: 1, heat: 0.4, update_ready: 1 }, now: 0 });
  s = reactionsReducer(s, { type: 'tick', now: 0.1 });
  const early = reactionSignals(s).thinking;
  assert.ok(early > 0 && early < 0.2, `thinking eased, got ${early}`);
  s = ticks(s, 0.2, 20);
  assert.equal(reactionSignals(s).thinking, 1);
  assert.equal(reactionSignals(s).ring2, 1);
  assert.ok(Math.abs(reactionSignals(s).heat - 0.4) < 0.005);
  assert.equal(isAnimating(s), false); // settled: the driver drops to its slow tick
});

test('idle show tilts slowly after a few minutes; input brings it back quickly', () => {
  let s = initialReactions(0, 13);
  s = ticks(s, 1, IDLE_S - 1, 1);
  assert.equal(reactionSignals(s).tilt, 0);
  s = ticks(s, IDLE_S, IDLE_S + 5, 0.1);
  const leaning = reactionSignals(s).tilt;
  assert.ok(leaning > 0.1 && leaning < 0.6, `slow lean, got ${leaning}`);
  s = ticks(s, IDLE_S + 5.1, IDLE_S + 60, 0.5);
  assert.ok(reactionSignals(s).tilt > 0.95);
  s = reactionsReducer(s, { type: 'input', now: IDLE_S + 60 });
  s = ticks(s, IDLE_S + 60.1, IDLE_S + 68, 0.1);
  assert.equal(reactionSignals(s).tilt, 0);
});

test('calm motion: no idle tilt, comets do not travel', () => {
  let s = initialReactions(0, 13, true);
  s = ticks(s, 1, IDLE_S + 60, 1);
  assert.equal(reactionSignals(s).tilt, 0);
  s = run(s, { type: 'comet', size: 'normal', now: s.now });
  const t0 = s.now;
  s = ticks(s, t0 + 0.5, t0 + 0.5);
  const a = reactionBodies(s);
  s = ticks(s, t0 + 1.5, t0 + 1.5);
  const b = reactionBodies(s);
  assert.equal(a[0], b[0]);
  assert.equal(a[1], b[1]);
  assert.equal(a[3] % 1, 0); // no tail
});

test('a comet spirals in and is gone; a batch is capped at three', () => {
  let s = initialReactions(0, 13);
  s = run(s, { type: 'comet', size: 'ember', now: 0 });
  s = ticks(s, 0.1, 1);
  const first = reactionBodies(s);
  const r1 = Math.hypot(first[0], first[1]);
  assert.equal(Math.floor(first[3]), 1); // ember colour
  s = ticks(s, 1.1, 4);
  const later = reactionBodies(s);
  assert.ok(Math.hypot(later[0], later[1]) < r1, 'moves inward');
  s = ticks(s, 4.1, 8);
  assert.equal(s.comets.length, 0);
  s = run(s, { type: 'comet', size: 'tiny', count: 40, now: s.now });
  assert.equal(s.comets.length, 3);
});

test('approvals orbit (max 7), approve falls in with a flash, reject drifts away', () => {
  let s = initialReactions(0, 13);
  const orbit = Array.from({ length: 10 }, (_, i) => ({ id: `a${i}`, tone: i === 0 ? 'boardroom' : 'approval' }));
  s = run(s, { type: 'signals', msg: { orbit, orbit_total: 12 }, now: 0 });
  assert.equal(s.stars.length, ORBIT_MAX);
  assert.equal(s.orbitTotal, 12);
  s = ticks(s, 0.1, 2);
  assert.equal(bodyCount(reactionBodies(s)), ORBIT_MAX);
  const angles = s.stars.map((st) => st.angle).sort((x, y) => x - y);
  for (let i = 1; i < angles.length; i++) assert.ok(angles[i] - angles[i - 1] > 0.3, 'stars spread out');

  s = run(s, { type: 'resolve', id: 'a1', outcome: 'approve', now: s.now });
  s = run(s, { type: 'resolve', id: 'a2', outcome: 'reject', now: s.now });
  assert.equal(s.stars.length, ORBIT_MAX - 2);
  assert.equal(s.orbitTotal, 10);
  // a stale snapshot that still lists them must not bring them back
  s = run(s, { type: 'signals', msg: { orbit, orbit_total: 12 }, now: s.now });
  assert.ok(!s.stars.some((st) => st.id === 'a1' || st.id === 'a2'));
  const t0 = s.now;
  s = ticks(s, t0 + 0.1, t0 + 1.7);
  assert.ok(s.flare > 0.3, 'flash when the approved star falls in');
  s = ticks(s, t0 + 1.8, t0 + 4);
  assert.equal(s.leaving.length, 0);
});

test('feeding the hole flares and fades; paused passes through', () => {
  let s = initialReactions(0, 13);
  s = run(s, { type: 'feed', now: 0 }, { type: 'signals', msg: { paused: true }, now: 0 });
  assert.ok(reactionSignals(s).flare >= 0.8);
  assert.equal(reactionSignals(s).paused, 1);
  s = ticks(s, 0.1, 8);
  assert.equal(reactionSignals(s).flare, 0);
});
