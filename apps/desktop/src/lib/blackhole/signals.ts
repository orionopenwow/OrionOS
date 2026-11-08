/**
 * Live inputs for every black hole on screen (HUD core, greeter, lock).
 *
 * Anything in the app can push a signal; every mounted renderer picks it up on its next frame:
 *
 *   import { setBlackHoleSignals } from '../lib/blackhole';
 *   setBlackHoleSignals({ voice: micLevel });          // 0..1, hot mic level -> ring brightens
 *   setBlackHoleSignals({ ripple: ttsLevel });         // 0..1, Orion speaking -> ripples through the disk
 *   setBlackHoleSignals({ thinking: 1 });              // models working -> a bit faster spin, warmer ring
 *   setBlackHoleSignals({ heat: spendThisHour });      // 0..1 compute spend -> hotter colour
 *   setBlackHoleSignals({ turbulence: cpuLoad });      // 0..1 system load
 *   setBlackHoleSignals({ dim: lowBattery ? 0.4 : 0 }) // 0..1 smaller and dimmer
 *   setBlackHoleSignals({ tilt: 1 });                  // idle show: edge-on view
 *   setBlackHoleSignals({ flare: 1 });                 // the disk flares (fed a file, approval fell in)
 *   setBlackHoleSignals({ ring2: 1 });                 // soft second ring: an update is ready
 *   setBlackHoleSignals({ quiet: 1 });                 // quiet hours: dimmer and slower
 *   setBlackHoleSignals({ night: 1, dawn: 0 });        // time of day: cooler violet at night, cyan touch at dawn
 *   setBlackHoleSignals({ paused: 1 });                // a full-screen game is in front: stop drawing
 *   setBlackHoleBodies(floats);                        // comets and approval stars (see reactions.ts)
 *
 * Stage 6 (smart reactions) feeds these from the daemon through one reducer (reactions.ts, useCoreReactions.ts),
 * so every value eases smoothly; the visual mapping lives in `applySignals`.
 */
import type { BlackHoleUniforms } from './renderer';

export interface BlackHoleSignals {
  voice: number;
  ripple: number;
  thinking: number;
  heat: number;
  turbulence: number;
  dim: number;
  tilt: number;
  flare: number;
  ring2: number;
  quiet: number;
  night: number;
  dawn: number;
  paused: number;
}

const signals: BlackHoleSignals = {
  voice: 0, ripple: 0, thinking: 0, heat: 0, turbulence: 0, dim: 0, tilt: 0,
  flare: 0, ring2: 0, quiet: 0, night: 0, dawn: 0, paused: 0,
};
const listeners = new Set<(s: Readonly<BlackHoleSignals>) => void>();

export function setBlackHoleSignals(patch: Partial<BlackHoleSignals>) {
  let changed = false;
  for (const [k, v] of Object.entries(patch) as [keyof BlackHoleSignals, number][]) {
    const clamped = Math.max(0, Math.min(1, Number.isFinite(v) ? v : 0));
    if (signals[k] !== clamped) {
      signals[k] = clamped;
      changed = true;
    }
  }
  if (changed) for (const l of listeners) l(signals);
}

export function getBlackHoleSignals(): Readonly<BlackHoleSignals> {
  return signals;
}

export function subscribeBlackHoleSignals(cb: (s: Readonly<BlackHoleSignals>) => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

// ------------------------------------------------------------------ small bodies (comets, approval stars)
let bodies: Float32Array = new Float32Array(0);
const bodyListeners = new Set<(b: Float32Array) => void>();

/** 4 floats per body (x, y, brightness, colour + tail), already animated by the caller. */
export function setBlackHoleBodies(b: Float32Array) {
  if (b.length === bodies.length && b.every((v, i) => v === bodies[i])) return;
  bodies = b.slice();
  for (const l of bodyListeners) l(bodies);
}

export function getBlackHoleBodies(): Float32Array {
  return bodies;
}

export function subscribeBlackHoleBodies(cb: (b: Float32Array) => void): () => void {
  bodyListeners.add(cb);
  return () => bodyListeners.delete(cb);
}

/** Layer the live signals over a mood preset. Every effect is gentle on purpose (calm liveliness). */
export function applySignals(base: BlackHoleUniforms, s: Readonly<BlackHoleSignals>): BlackHoleUniforms {
  // night cools the resting look, but leaves a warm mood (winddown, sanctuary) mostly as it is
  const cool = 0.22 * s.night * (1 - Math.max(0, Math.min(1, base.temp)));
  return {
    ...base,
    spin: base.spin * (1 + 0.5 * s.thinking) * (1 - 0.5 * s.dim) * (1 - 0.35 * s.quiet),
    temp: Math.max(-1, Math.min(1, base.temp + 0.2 * s.thinking + 0.45 * s.heat - cool)),
    hue: base.hue - 0.3 * s.dawn,
    ring: base.ring * (1 + 0.2 * s.thinking) * (1 - 0.4 * s.dim) * (1 - 0.2 * s.quiet),
    glow: base.glow * (1 - 0.5 * s.dim) * (1 - 0.3 * s.quiet),
    turbulence: Math.max(base.turbulence, s.turbulence),
    size: base.size * (1 - 0.15 * s.dim),
    tilt: Math.max(base.tilt, s.tilt),
    voice: Math.max(base.voice, s.voice),
    ripple: Math.max(base.ripple, s.ripple),
    flare: Math.max(base.flare, s.flare),
    ring2: Math.max(base.ring2, s.ring2),
  };
}
