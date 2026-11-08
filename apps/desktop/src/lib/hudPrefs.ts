/**
 * Look & motion settings for the HUD (Settings window): rare sky events, parallax, depth and calm motion.
 * Saved in localStorage; "calm motion" also follows the system's reduced-motion setting (§8 of the Art Update).
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

export interface HudPrefs {
  /** A shooting star or a comet around the hole, a few times an hour at most. */
  rareSkyEvents: boolean;
  /** Stars drift slightly with the mouse (or tilt on a tablet). */
  parallax: boolean;
  /** Simple fades only, even if the system doesn't ask for reduced motion. */
  calmMotion: boolean;
  /** Depth: the screen edges stay flat and space curves toward the core (SpaceCurve). */
  depth: boolean;
}

const KEY = 'orion_hud_prefs';
const DEFAULTS: HudPrefs = { rareSkyEvents: true, parallax: true, calmMotion: false, depth: true };

function load(): HudPrefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<HudPrefs>) };
  } catch {
    /* private mode / no storage */
  }
  return DEFAULTS;
}

let prefs: HudPrefs = typeof window === 'undefined' ? DEFAULTS : load();
const listeners = new Set<() => void>();

export const hudPrefs = {
  get: () => prefs,
  set(patch: Partial<HudPrefs>) {
    prefs = { ...prefs, ...patch };
    try {
      window.localStorage.setItem(KEY, JSON.stringify(prefs));
    } catch {
      /* ignore */
    }
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
};

export function useHudPrefs(): HudPrefs {
  return useSyncExternalStore(hudPrefs.subscribe, hudPrefs.get, hudPrefs.get);
}

const RM = '(prefers-reduced-motion: reduce)';

export function systemReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(RM).matches;
}

/** True when animations should collapse to simple fades (system setting or the HUD's own toggle). */
export function calmMotionNow(): boolean {
  return prefs.calmMotion || systemReducedMotion();
}

export function useCalmMotion(): boolean {
  const p = useHudPrefs();
  const [sys, setSys] = useState(systemReducedMotion);
  useEffect(() => {
    const q = window.matchMedia?.(RM);
    if (!q) return;
    const on = () => setSys(q.matches);
    q.addEventListener('change', on);
    return () => q.removeEventListener('change', on);
  }, []);
  return p.calmMotion || sys;
}
