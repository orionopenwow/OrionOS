/**
 * The one reducer behind the black hole's smart reactions (Art Update §5, build step 6). Pure and clock-free:
 * every action carries its own time, so it can be tested without a browser.
 *
 * The daemon (apps/daemon/core_signals.py) sends CORE_SIGNALS (levels, pending approvals) and CORE_EVENT (a
 * comet, an approval decided, a file fed to the vault). The HUD adds input (idle show), the local time (day and
 * night) and the calm-motion setting. Everything eases here, gently, and comes out as:
 *   reactionSignals(state) -> setBlackHoleSignals(...)   levels for the shader
 *   reactionBodies(state)  -> setBlackHoleBodies(...)    comets and approval stars for u_bodies
 *
 * Calm motion (the HUD setting or the system's reduced motion): comets and stars don't travel, they just fade in
 * and out where they are; no idle tilt; smaller flares.
 */

export type CometSize = 'ember' | 'normal' | 'tiny';
export type OrbitTone = 'approval' | 'boardroom';
export type Outcome = 'approve' | 'reject' | 'fade';

export interface CoreSignalsMessage {
  thinking?: number;
  heat?: number;
  turbulence?: number;
  dim?: number;
  update_ready?: number;
  quiet?: number;
  paused?: boolean;
  orbit?: Array<{ id: string; tone?: string }>;
  orbit_total?: number;
}

type LevelKey = 'thinking' | 'heat' | 'turbulence' | 'dim' | 'ring2' | 'quiet' | 'tilt' | 'night' | 'dawn';
type Levels = Record<LevelKey, number>;

interface Comet {
  born: number;
  dur: number;
  size: CometSize;
  angle: number;
}

interface Star {
  id: string;
  tone: OrbitTone;
  born: number;
  angle: number;
}

interface Leaving {
  id: string;
  tone: OrbitTone;
  outcome: Outcome;
  born: number;
  angle: number;
  r: number;
}

export interface ReactionState {
  now: number;
  calm: boolean;
  paused: boolean;
  target: Levels;
  level: Levels;
  flare: number;
  lastInput: number;
  orbitPhase: number;
  comets: Comet[];
  stars: Star[];
  leaving: Leaving[];
  orbitTotal: number;
  /** ids decided recently: a snapshot sent before the decision must not bring the star back */
  gone: Record<string, number>;
  seq: number;
}

export type ReactionAction =
  | { type: 'signals'; msg: CoreSignalsMessage; now?: number }
  | { type: 'comet'; size: CometSize; count?: number; now?: number }
  | { type: 'resolve'; id: string; outcome: 'approve' | 'reject'; now?: number }
  | { type: 'feed'; strength?: number; now?: number }
  | { type: 'input'; now: number }
  | { type: 'calm'; on: boolean }
  | { type: 'tick'; now: number; hour?: number };

// ------------------------------------------------------------------ tuning (calm liveliness)
export const IDLE_S = 180;          // idle show starts after three quiet minutes
export const ORBIT_MAX = 7;
export const ORBIT_R = 0.43;        // just outside the painted disk (shader units: 0.5 = frame edge)
const ORBIT_SPEED = 0.045;          // rad/s: one lap in about two and a half minutes
const COMET_R0 = 0.47;
const COMET_R1 = 0.088;             // the inner edge, where the horizon takes it
const MAX_COMETS = 3;
const TAU_DEFAULT = 2.5;
const TAU: Partial<Record<LevelKey, number>> = { quiet: 6, night: 30, dawn: 30, ring2: 3 };
const TILT_IN = 14;                 // seconds: the slow lean to edge-on
const TILT_OUT = 1.2;               // any input brings it back promptly
const FLARE_TAU = 0.9;
const COMET_DUR: Record<CometSize, number> = { ember: 6.5, normal: 6, tiny: 4.5 };
const COMET_GLOW: Record<CometSize, number> = { ember: 1, normal: 0.8, tiny: 0.45 };
const COMET_TAIL: Record<CometSize, number> = { ember: 0.55, normal: 0.4, tiny: 0.2 };
const COMET_CODE: Record<CometSize, number> = { ember: 1, normal: 0, tiny: 3 };
const LEAVE_DUR: Record<Outcome, number> = { approve: 1.6, reject: 2.8, fade: 1.0 };
const TWO_PI = Math.PI * 2;

const LEVEL_KEYS: LevelKey[] = ['thinking', 'heat', 'turbulence', 'dim', 'ring2', 'quiet', 'tilt', 'night', 'dawn'];

function zeroLevels(): Levels {
  return { thinking: 0, heat: 0, turbulence: 0, dim: 0, ring2: 0, quiet: 0, tilt: 0, night: 0, dawn: 0 };
}

const clamp01 = (x: number) => (Number.isFinite(x) ? Math.max(0, Math.min(1, x)) : 0);
const smooth = (e0: number, e1: number, x: number) => {
  const t = clamp01((x - e0) / (e1 - e0));
  return t * t * (3 - 2 * t);
};

/** Local hour (0..24, fractional) -> how night-ish and how dawn-ish it is. */
export function dayTone(hour: number): { night: number; dawn: number } {
  const h = ((hour % 24) + 24) % 24;
  const night = h >= 12 ? smooth(20, 22, h) : 1 - smooth(5, 6.5, h);
  const dawn = smooth(0, 1, 1 - Math.abs(h - 6.5) / 1.25);
  return { night: round(night), dawn: round(dawn) };
}

function round(x: number) {
  return Math.round(x * 1000) / 1000;
}

/** A pseudo-random but repeatable angle for the n-th comet. */
function seededAngle(seq: number) {
  const x = Math.sin(seq * 12.9898 + 78.233) * 43758.5453;
  return (x - Math.floor(x)) * TWO_PI;
}

/** A new star takes the middle of the widest gap, so stars never bunch up. */
function freeAngle(taken: number[]): number {
  if (!taken.length) return Math.PI * 0.25;
  const a = taken.map((t) => ((t % TWO_PI) + TWO_PI) % TWO_PI).sort((x, y) => x - y);
  let best = 0;
  let at = 0;
  for (let i = 0; i < a.length; i++) {
    const next = i + 1 < a.length ? a[i + 1] : a[0] + TWO_PI;
    if (next - a[i] > best) {
      best = next - a[i];
      at = a[i] + best / 2;
    }
  }
  return at % TWO_PI;
}

export function initialReactions(now: number, hour?: number, calm = false): ReactionState {
  const level = zeroLevels();
  if (hour !== undefined) Object.assign(level, dayTone(hour));
  return {
    now,
    calm,
    paused: false,
    target: { ...level },
    level,
    flare: 0,
    lastInput: now,
    orbitPhase: 0,
    comets: [],
    stars: [],
    leaving: [],
    orbitTotal: 0,
    gone: {},
    seq: 0,
  };
}

function starAngle(s: ReactionState, star: Star) {
  return star.angle + s.orbitPhase;
}

export function reactionsReducer(s: ReactionState, a: ReactionAction): ReactionState {
  switch (a.type) {
    case 'signals': {
      const m = a.msg;
      const now = a.now ?? s.now;
      const target = { ...s.target };
      if (m.thinking !== undefined) target.thinking = clamp01(m.thinking);
      if (m.heat !== undefined) target.heat = clamp01(m.heat);
      if (m.turbulence !== undefined) target.turbulence = clamp01(m.turbulence);
      if (m.dim !== undefined) target.dim = clamp01(m.dim);
      if (m.update_ready !== undefined) target.ring2 = m.update_ready >= 0.5 ? 1 : 0;
      if (m.quiet !== undefined) target.quiet = m.quiet >= 0.5 ? 1 : 0;
      let { stars, leaving, orbitTotal } = s;
      if (m.orbit) {
        const wanted = m.orbit.filter((o) => o && o.id && !(o.id in s.gone)).slice(0, ORBIT_MAX);
        const ids = new Set(wanted.map((o) => o.id));
        const keep = stars.filter((st) => ids.has(st.id));
        const dropped = stars.filter((st) => !ids.has(st.id));
        leaving = [
          ...leaving,
          ...dropped.map((st) => ({ id: st.id, tone: st.tone, outcome: 'fade' as Outcome, born: now, angle: starAngle(s, st), r: ORBIT_R })),
        ];
        const next = [...keep];
        for (const o of wanted) {
          if (next.some((st) => st.id === o.id)) continue;
          const angle = freeAngle(next.map((st) => st.angle));
          next.push({ id: o.id, tone: o.tone === 'boardroom' ? 'boardroom' : 'approval', born: now, angle });
        }
        stars = next;
        orbitTotal = Math.max(next.length, Math.round(m.orbit_total ?? next.length));
      } else if (m.orbit_total !== undefined) {
        orbitTotal = Math.max(stars.length, Math.round(m.orbit_total));
      }
      return { ...s, target, stars, leaving, orbitTotal, paused: m.paused ?? s.paused };
    }
    case 'comet': {
      const now = a.now ?? s.now;
      const n = Math.max(1, Math.min(MAX_COMETS, Math.round(a.count ?? 1)));
      const fresh: Comet[] = [];
      let seq = s.seq;
      for (let k = 0; k < n; k++) {
        seq++;
        fresh.push({
          born: now + k * 0.55,
          dur: s.calm ? 3 : COMET_DUR[a.size],
          size: k === 0 ? a.size : a.size === 'ember' ? 'normal' : 'tiny', // a batch: one leads, the rest are small
          angle: seededAngle(seq),
        });
      }
      let comets = [...s.comets, ...fresh];
      // never more than MAX_COMETS at once: the oldest non-ember ones make room
      while (comets.length > MAX_COMETS) {
        const i = comets.findIndex((c) => c.size !== 'ember');
        comets.splice(i >= 0 ? i : 0, 1);
      }
      comets = [...comets];
      return { ...s, comets, seq };
    }
    case 'resolve': {
      const now = a.now ?? s.now;
      const star = s.stars.find((st) => st.id === a.id);
      const gone = { ...s.gone, [a.id]: now + 15 };
      if (!star) return { ...s, gone };
      return {
        ...s,
        gone,
        stars: s.stars.filter((st) => st.id !== a.id),
        leaving: [...s.leaving, { id: star.id, tone: star.tone, outcome: a.outcome, born: now, angle: starAngle(s, star), r: ORBIT_R }],
        orbitTotal: Math.max(0, s.orbitTotal - 1),
      };
    }
    case 'feed': {
      const strength = clamp01(a.strength ?? 0.8) * (s.calm ? 0.55 : 1);
      return { ...s, flare: Math.max(s.flare, strength) };
    }
    case 'input': {
      if (a.now - s.lastInput < 0.25 && s.target.tilt === 0) return s; // nothing to undo: avoid churn
      return { ...s, lastInput: a.now, target: { ...s.target, tilt: 0 } };
    }
    case 'calm':
      return a.on === s.calm ? s : { ...s, calm: a.on };
    case 'tick':
      return tick(s, a.now, a.hour);
  }
}

function tick(s: ReactionState, now: number, hour?: number): ReactionState {
  const dt = Math.max(0, Math.min(0.5, now - s.now));
  const target = { ...s.target };
  if (hour !== undefined) Object.assign(target, dayTone(hour));
  target.tilt = !s.calm && now - s.lastInput >= IDLE_S ? 1 : 0;
  const level = { ...s.level };
  for (const k of LEVEL_KEYS) {
    const tau = k === 'tilt' ? (target.tilt > level.tilt ? TILT_IN : TILT_OUT) : TAU[k] ?? TAU_DEFAULT;
    const kk = 1 - Math.exp(-dt / tau);
    let v = level[k] + (target[k] - level[k]) * kk;
    if (Math.abs(target[k] - v) < 0.002) v = target[k];
    level[k] = v;
  }
  let flare = s.flare * Math.exp(-dt / FLARE_TAU);
  if (flare < 0.002) flare = 0;
  const comets = s.comets.filter((c) => now - c.born < c.dur);
  const leaving: Leaving[] = [];
  for (const l of s.leaving) {
    const dur = (s.calm ? 0.6 : 1) * LEAVE_DUR[l.outcome];
    if (now - l.born < dur) leaving.push(l);
    else if (l.outcome === 'approve') flare = Math.max(flare, s.calm ? 0.3 : 0.55); // it fell in: a flash
  }
  const gone: Record<string, number> = {};
  for (const [id, until] of Object.entries(s.gone)) if (until > now) gone[id] = until;
  const orbitPhase = s.calm ? s.orbitPhase : (s.orbitPhase + dt * ORBIT_SPEED) % TWO_PI;
  return { ...s, now, target, level, flare, comets, leaving, gone, orbitPhase };
}

/** Is anything moving? (The driver runs at frame rate only then, otherwise about once a second.) */
export function isAnimating(s: ReactionState): boolean {
  if (s.comets.length || s.leaving.length || s.flare > 0) return true;
  if (s.stars.length && !s.calm) return true;
  if (s.stars.some((st) => s.now - st.born < 1.2)) return true;
  return LEVEL_KEYS.some((k) => s.level[k] !== s.target[k]);
}

/** Levels for setBlackHoleSignals (rounded so a settled value stops notifying). */
export function reactionSignals(s: ReactionState) {
  const l = s.level;
  return {
    thinking: round(l.thinking),
    heat: round(l.heat),
    turbulence: round(l.turbulence),
    dim: round(l.dim),
    ring2: round(l.ring2),
    quiet: round(l.quiet),
    tilt: round(l.tilt),
    night: round(l.night),
    dawn: round(l.dawn),
    flare: round(s.flare),
    paused: s.paused ? 1 : 0,
  };
}

/** Comets, departing stars and orbiting stars as u_bodies floats (x, y, brightness, colour + tail). */
export function reactionBodies(s: ReactionState, slots = 10): Float32Array {
  const out = new Float32Array(slots * 4);
  let i = 0;
  const put = (angle: number, r: number, glow: number, code: number, tail: number) => {
    if (i >= slots || glow <= 0.001) return;
    out[i * 4] = Math.cos(angle) * r;
    out[i * 4 + 1] = Math.sin(angle) * r;
    out[i * 4 + 2] = Math.min(1.5, glow);
    out[i * 4 + 3] = code + Math.min(0.95, Math.max(0, tail));
    i++;
  };
  const now = s.now;
  for (const c of s.comets) {
    const u = (now - c.born) / c.dur;
    if (u < 0 || u >= 1) continue;
    if (s.calm) {
      // calm: a soft glint near the disk that fades in and out where it is
      const fade = smooth(0, 0.2, u) * (1 - smooth(0.65, 1, u));
      put(c.angle, 0.3, COMET_GLOW[c.size] * fade, COMET_CODE[c.size], 0);
      continue;
    }
    const fall = Math.pow(u, 1.6);
    const r = COMET_R0 + (COMET_R1 - COMET_R0) * fall;
    const angle = c.angle + 0.9 * u + 1.8 * u * u; // speeds up as it spirals in
    const fade = smooth(0, 0.12, u) * (1 - smooth(0.85, 1, u));
    put(angle, r, COMET_GLOW[c.size] * fade, COMET_CODE[c.size], COMET_TAIL[c.size]);
  }
  for (const l of s.leaving) {
    const dur = (s.calm ? 0.6 : 1) * LEAVE_DUR[l.outcome];
    const u = Math.max(0, Math.min(1, (now - l.born) / dur));
    const code = l.tone === 'boardroom' ? 2 : 0;
    if (s.calm || l.outcome === 'fade') {
      put(l.angle, l.r, (l.outcome === 'approve' ? 1.1 : 0.9) * (1 - u), code, 0);
    } else if (l.outcome === 'approve') {
      const r = l.r + (COMET_R1 - l.r) * u * u;
      put(l.angle + 1.4 * u * u, r, 0.95 + 0.35 * u, code, 0.15 + 0.2 * u);
    } else {
      put(l.angle + 0.05 * u, l.r + 0.09 * u, 0.9 * (1 - u) * (1 - u), code, 0);
    }
  }
  for (const st of s.stars) {
    const appear = smooth(0, 1.2, now - st.born);
    const twinkle = s.calm ? 1 : 0.88 + 0.12 * Math.sin(now * 1.7 + st.angle * 3);
    put(starAngle(s, st), ORBIT_R, 0.85 * appear * twinkle, st.tone === 'boardroom' ? 2 : 0, 0);
  }
  return out;
}
