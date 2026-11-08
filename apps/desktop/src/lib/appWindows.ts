/**
 * Real app windows on OrionOS (review H13): the HUD taskbar lists them next to Orion's own windows, and a click
 * brings one to the front (Sway switches to its workspace; a minimised one comes back). The daemon reads them from
 * Sway (GET /linux/windows, desktop_shell.py). Off OrionOS the list is simply empty and polling slows right down.
 */
import { useEffect, useState } from 'react';
import { ORION_HTTP_URL } from './assets';

export interface AppWindow {
  id: number;
  app: string;
  app_id: string;
  title: string;
  workspace: string | null;
  focused: boolean;
  minimised: boolean;
  urgent: boolean;
}

export function useAppWindows(enabled = true): AppWindow[] {
  const [wins, setWins] = useState<AppWindow[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      let next = 30000;
      try {
        const r = await fetch(`${ORION_HTTP_URL}/linux/windows`);
        const body = r.ok ? await r.json() : null;
        if (alive && body && Array.isArray(body.windows)) {
          setWins(body.windows as AppWindow[]);
          if (body.available) next = 3000;
        }
      } catch {
        /* daemon not up yet */
      }
      if (alive) timer = setTimeout(poll, next);
    };
    poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [enabled]);
  return wins;
}

export async function focusAppWindow(id: number): Promise<boolean> {
  try {
    const r = await fetch(`${ORION_HTTP_URL}/linux/windows/${id}/focus`, { method: 'POST' });
    return r.ok;
  } catch {
    return false;
  }
}

/** A steady colour per app, so the same app always gets the same tile colour. */
export function appColor(appId: string): string {
  const palette = ['#8DB0FF', '#7FF2FF', '#5CFFB0', '#FFC66B', '#FF8FB0', '#B9C0E8'];
  let h = 0;
  for (const ch of appId) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return palette[h % palette.length];
}
