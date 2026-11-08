/**
 * The HUD's existing modes as shader settings (plan §2 "Keeps today's moods"). Skins re-tint the resting look.
 */
import { DEFAULT_UNIFORMS, type BlackHoleUniforms } from './renderer';

export type BlackHoleMode = 'idle' | 'focus' | 'voice' | 'podcast' | 'winddown' | 'sanctuary';

type Preset = Partial<BlackHoleUniforms>;

export const MODE_PRESETS: Record<BlackHoleMode, Preset> = {
  // dormant supermassive black hole: slow violet swirl
  idle: {},
  // quasar: electric cyan accretion, faster, brighter ring
  focus: { spin: 1.5, ring: 1.35, glow: 1.15, temp: -0.3, hue: -0.9, turbulence: 0.35 },
  // pulsar: the ring follows your voice (u_voice is fed live), a touch faster
  voice: { spin: 1.3, ring: 1.15, glow: 1.1, temp: -0.15, hue: -0.35 },
  // binary merger / flare: magenta-gold, lively
  podcast: { spin: 1.35, ring: 1.2, glow: 1.1, temp: 0.45, turbulence: 0.45 },
  // redshift nightlight: near-halted crimson/amber, dim
  winddown: { spin: 0.25, ring: 0.5, glow: 0.3, temp: 0.75, turbulence: 0.1 },
  // recovery: slow, deep soothing amber
  sanctuary: { spin: 0.5, ring: 0.85, glow: 0.8, temp: 0.95, turbulence: 0.15 },
};

/** Skins unlocked through Architect XP; they re-tint only the resting (idle) look. */
export const SKIN_PRESETS: Record<string, Preset> = {
  default: {},
  emerald: { hue: -2.0, temp: -0.1 },
  supernova: { temp: 1, ring: 1.25, glow: 1.2 },
  bloodmoon: { temp: 0.7, glow: 0.9, hue: 0.15 },
  runic: { hue: -0.5, ring: 1.2, glow: 1.1 },
};

export function presetFor(mode: BlackHoleMode, skin = 'default', processing = false): BlackHoleUniforms {
  const u: BlackHoleUniforms = { ...DEFAULT_UNIFORMS, ...MODE_PRESETS[mode] };
  if (mode === 'idle') Object.assign(u, SKIN_PRESETS[skin] ?? {});
  if (processing) {
    u.spin *= 1.8;
    u.ring *= 1.2;
    u.temp = Math.min(1, u.temp + 0.2);
  }
  return u;
}
