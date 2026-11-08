/**
 * Where the one Orion button sits (look pass). It can be dragged anywhere; the spot is remembered in
 * localStorage (the Tauri webview has it too) and pulled back inside the screen when the window shrinks.
 * The console grows out of this spot, and the taskbar moves aside when the button is in its corner.
 * Pure helpers (clampPos, readPos) have no DOM needs so tests can run them with `node --test`.
 */
export const BUTTON_SIZE = 58;
export const BUTTON_MARGIN = 12;
export const BUTTON_STORAGE_KEY = 'orion.buttonPos';

export interface Pos {
  x: number;
  y: number;
}

/** Keep the whole button on screen with a small margin. */
export function clampPos(p: Pos, w: number, h: number, size = BUTTON_SIZE, margin = BUTTON_MARGIN): Pos {
  const maxX = Math.max(margin, w - size - margin);
  const maxY = Math.max(margin, h - size - margin);
  return { x: Math.round(Math.min(maxX, Math.max(margin, p.x))), y: Math.round(Math.min(maxY, Math.max(margin, p.y))) };
}

/** The resting spot: bottom left. */
export function defaultPos(w: number, h: number): Pos {
  return clampPos({ x: 16, y: h - BUTTON_SIZE - 16 }, w, h);
}

/** Parse a saved spot; anything odd falls back to the default. */
export function readPos(raw: string | null, w: number, h: number): Pos {
  try {
    const v = raw ? (JSON.parse(raw) as Partial<Pos>) : null;
    if (v && Number.isFinite(v.x) && Number.isFinite(v.y)) return clampPos({ x: v.x as number, y: v.y as number }, w, h);
  } catch {
    /* fall through */
  }
  return defaultPos(w, h);
}

/** Is the button parked in the bottom-left corner (where the taskbar starts)? */
export function inTaskbarCorner(p: Pos, h: number): boolean {
  return p.x < 90 && p.y > h - BUTTON_SIZE - 40;
}

// ---- a tiny shared store ----------------------------------------------------------------------------
let current: Pos | null = null;
const listeners = new Set<(p: Pos) => void>();

export function getButtonPos(): Pos {
  if (current) return current;
  if (typeof window === 'undefined') return { x: 16, y: 600 };
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(BUTTON_STORAGE_KEY);
  } catch {
    /* storage blocked */
  }
  current = readPos(raw, window.innerWidth, window.innerHeight);
  return current;
}

export function setButtonPos(p: Pos, save = false): void {
  current = p;
  if (save) {
    try {
      localStorage.setItem(BUTTON_STORAGE_KEY, JSON.stringify(p));
    } catch {
      /* remembered for now only */
    }
  }
  listeners.forEach((fn) => fn(p));
}

export function subscribeButtonPos(fn: (p: Pos) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
