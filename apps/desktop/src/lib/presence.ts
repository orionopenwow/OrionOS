/**
 * Presence (after-1.0 plan §6, Phase 5): talk to the daemon's /presence routes (apps/daemon/presence.py).
 * The camera is off by default; everything runs on this computer and no picture ever reaches the HUD.
 */
import { ORION_HTTP_URL } from './assets';

export interface PresenceStatus {
  on: boolean;
  running: boolean;
  paused: string;
  error: string;
  missing: string[];
  camera_switch: string | null;
  present: boolean | null;
  looking_away: boolean;
  screen_time_today_min: number;
  last_gesture: { gesture: string; at: number; [k: string]: unknown } | null;
  calibrating: boolean;
  config: Record<string, unknown> & { zone: number[] };
}

export interface Calibration {
  on: boolean;
  zone: number[];
  hand: { x: number; y: number; pose: string } | null;
  armed: boolean;
  calibrating: boolean;
  samples: number;
  ok?: boolean;
  message?: string;
}

async function call<T>(path: string, body?: unknown): Promise<T | null> {
  try {
    const r = await fetch(`${ORION_HTTP_URL}${path}`, body === undefined ? undefined : {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null; // daemon not up yet
  }
}

export const presenceStatus = () => call<PresenceStatus>('/presence');
export const presenceSwitch = (action: 'on' | 'off' | 'toggle') => call<PresenceStatus>('/presence', { action });
export const presenceConfig = (changes: Record<string, unknown>) => call<PresenceStatus>('/presence/config', changes);
export const presenceCalibration = () => call<Calibration>('/presence/calibration');
export const presenceCalibrate = (action: 'start' | 'finish' | 'reset') =>
  call<Calibration>('/presence/calibrate', { action });
/** Tell Orion which approval card is in front, so a thumbs up can only ever mean that card (if it's small). */
export const presenceCurrentCard = (id: string) => call<{ ok: boolean }>('/presence/current-card', { id });

/** Plain words for the gesture the camera just saw. */
export const GESTURE_WORDS: Record<string, string> = {
  stop: 'Open palm: Orion is frozen',
  swipe_left: 'Next desktop',
  swipe_right: 'Previous desktop',
  rectangle: 'New desktop',
  throw: 'Window moved',
  spread: 'Zoom in',
  squeeze: 'Zoom out',
  thumbs_up: 'Thumbs up',
  click: 'Click',
};
