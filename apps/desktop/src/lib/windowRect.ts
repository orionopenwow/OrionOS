/**
 * Window size and position the owner chose by dragging an edge, a corner or the title bar. Kept per window id
 * in localStorage (wrapped in try/catch: private windows and previews can throw), clamped to the screen.
 */
export interface WinRect { x: number; y: number; w: number; h: number }

export const MIN_W = 340;
export const MIN_H = 260;
const KEY = (id: string) => `orion.window.${id}`;

export function loadRect(id: string): WinRect | null {
  try {
    const raw = window.localStorage.getItem(KEY(id));
    if (!raw) return null;
    const r = JSON.parse(raw) as WinRect;
    return [r.x, r.y, r.w, r.h].every((n) => typeof n === 'number' && Number.isFinite(n)) ? r : null;
  } catch {
    return null;
  }
}

export function saveRect(id: string, r: WinRect): void {
  try {
    window.localStorage.setItem(KEY(id), JSON.stringify(r));
  } catch {
    /* storage unavailable: the size just isn't remembered */
  }
}

/** Keep a window fully reachable: at least its title bar on screen and never larger than the screen. */
export function clampRect(r: WinRect, vw: number, vh: number): WinRect {
  const w = Math.max(MIN_W, Math.min(r.w, vw - 16));
  const h = Math.max(MIN_H, Math.min(r.h, vh - 16));
  const x = Math.max(8, Math.min(r.x, vw - w - 8));
  const y = Math.max(8, Math.min(r.y, vh - h - 8));
  return { x, y, w, h };
}

export type Edge = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw' | 'move';

/** The new rect after dragging `edge` by (dx, dy) from `start`. */
export function dragRect(start: WinRect, edge: Edge, dx: number, dy: number): WinRect {
  let { x, y, w, h } = start;
  if (edge === 'move') return { x: x + dx, y: y + dy, w, h };
  if (edge.includes('e')) w = Math.max(MIN_W, start.w + dx);
  if (edge.includes('s')) h = Math.max(MIN_H, start.h + dy);
  if (edge.includes('w')) {
    w = Math.max(MIN_W, start.w - dx);
    x = start.x + (start.w - w);
  }
  if (edge.includes('n')) {
    h = Math.max(MIN_H, start.h - dy);
    y = start.y + (start.h - h);
  }
  return { x, y, w, h };
}
