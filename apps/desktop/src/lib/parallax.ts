/**
 * Shared parallax value (-1..1 on each axis): mouse position on desktop, device tilt on tablets.
 * The sky canvas moves its three star depths with it. Smoothed; reads 0,0 when parallax is off or
 * motion should be calm. Exported so other layers (e.g. the core's `u_parallax`) can use the same value.
 */
import { calmMotionNow, hudPrefs } from './hudPrefs';

const target = { x: 0, y: 0 };
const value = { x: 0, y: 0 };
let started = false;

function start() {
  if (started || typeof window === 'undefined') return;
  started = true;
  window.addEventListener(
    'pointermove',
    (e) => {
      if (e.pointerType === 'touch') return;
      target.x = (e.clientX / window.innerWidth) * 2 - 1;
      target.y = (e.clientY / window.innerHeight) * 2 - 1;
    },
    { passive: true },
  );
  // Tablets: tilt instead of a mouse (iOS needs a permission prompt, so it simply stays still there).
  if (window.matchMedia?.('(pointer: coarse)').matches) {
    window.addEventListener(
      'deviceorientation',
      (e) => {
        if (e.gamma == null || e.beta == null) return;
        target.x = Math.max(-1, Math.min(1, e.gamma / 30));
        target.y = Math.max(-1, Math.min(1, (e.beta - 45) / 30));
      },
      { passive: true },
    );
  }
}

/** Call once per frame; returns the smoothed value. */
export function stepParallax(): { x: number; y: number } {
  start();
  const on = hudPrefs.get().parallax && !calmMotionNow();
  const tx = on ? target.x : 0;
  const ty = on ? target.y : 0;
  value.x += (tx - value.x) * 0.04;
  value.y += (ty - value.y) * 0.04;
  return value;
}

export function getParallax(): { x: number; y: number } {
  return { x: value.x, y: value.y };
}
