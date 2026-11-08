/**
 * Arrival after login (Art Update §3, last row): the camera eases in, open windows fade in as glass and the
 * taskbar tiles rise in. Driven by CSS through `<html data-arrival="pending|playing|done">` (styles/glass.css).
 * In the desktop app it waits until Orion's brain is connected (the boot screen covers the HUD before that).
 */
import { useEffect, useState } from 'react';
import { isTauri } from './tauri';

export type ArrivalPhase = 'pending' | 'playing' | 'done';

const PLAY_MS = 2600;

// Set before the first paint so nothing flashes in at full strength before the arrival starts.
if (typeof document !== 'undefined') document.documentElement.dataset.arrival = 'pending';
const MAX_WAIT_MS = 20000;

export function arrivalPhase(): ArrivalPhase {
  const v = typeof document !== 'undefined' ? document.documentElement.dataset.arrival : undefined;
  return v === 'pending' || v === 'playing' ? v : 'done';
}

export function useArrival(connected: boolean): ArrivalPhase {
  const [phase, setPhase] = useState<ArrivalPhase>('pending');
  const ready = connected || !isTauri();

  useEffect(() => {
    if (phase !== 'pending') return;
    if (ready) {
      // Two frames so the "pending" styles are painted before the transition starts.
      let f2 = 0;
      const f1 = requestAnimationFrame(() => (f2 = requestAnimationFrame(() => setPhase('playing'))));
      return () => {
        cancelAnimationFrame(f1);
        cancelAnimationFrame(f2);
      };
    }
    const t = window.setTimeout(() => setPhase('playing'), MAX_WAIT_MS);
    return () => window.clearTimeout(t);
  }, [ready, phase]);

  useEffect(() => {
    if (phase !== 'playing') return;
    const t = window.setTimeout(() => setPhase('done'), PLAY_MS);
    return () => window.clearTimeout(t);
  }, [phase]);

  useEffect(() => {
    document.documentElement.dataset.arrival = phase;
  }, [phase]);

  return phase;
}
