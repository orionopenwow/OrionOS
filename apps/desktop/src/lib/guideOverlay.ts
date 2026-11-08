/**
 * Guide Mode on Windows: the pointer ring above every app (OrionOS 1.0 plan, D. "Guide Mode pointer ring").
 *
 * The desktop app opens two extra windows from the same page (main.tsx routes them by the URL's #hash):
 *  - `guide-ring`: full screen, transparent, always on top and click-through (setIgnoreCursorEvents), so clicks go
 *    to the app underneath. It only draws the glowing ring around the next thing to press.
 *  - `guide-card`: a small always-on-top card in the bottom-right corner with the step, Done, Back and Stop.
 * Both close when the guide ends. The guide's state is pushed to them with Tauri events (`orion://guide`).
 *
 * On OrionOS (Sway) Wayland doesn't allow apps to place always-on-top windows, so the ring there is the
 * gtk-layer-shell overlay os/desktop/orion-guide-ring, started by the daemon; these windows are not used.
 * In a plain browser (dev:web) the HUD draws the ring inside its own page instead.
 */
import { isTauri } from './tauri';
import type { GuideTarget } from './guideGeometry';

export { ringBox, type GuideTarget } from './guideGeometry';

export interface GuideState {
  active: boolean;
  id?: string;
  title?: string;
  index?: number;
  total?: number;
  text?: string;
  find?: string;
  auto?: boolean;
  target?: GuideTarget | null;
  source?: 'accessibility' | 'screen' | null;
  size?: [number, number] | null;
  withheld?: string;
  finished?: string;
}

export const GUIDE_EVENT = 'orion://guide';
export const RING_LABEL = 'guide-ring';
export const CARD_LABEL = 'guide-card';

/** Which kind of overlay this computer gets. */
export function overlayKind(): 'tauri' | 'layer-shell' | 'in-page' {
  if (!isTauri()) return 'in-page';
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : '';
  return /Linux/i.test(ua) && !/Android/i.test(ua) ? 'layer-shell' : 'tauri';
}

async function ensureWindow(label: string, opts: Record<string, unknown>): Promise<void> {
  const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow');
  if (await WebviewWindow.getByLabel(label)) return;
  const w = new WebviewWindow(label, opts as never);
  await new Promise<void>((resolve) => {
    void w.once('tauri://created', () => resolve());
    void w.once('tauri://error', () => resolve());
  });
}

async function closeWindow(label: string): Promise<void> {
  const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow');
  const w = await WebviewWindow.getByLabel(label);
  if (w) await w.close();
}

/** Keep the Windows overlay windows in step with the guide (no-op elsewhere). */
export async function syncOverlay(state: GuideState): Promise<void> {
  if (overlayKind() !== 'tauri') return;
  try {
    if (!state.active) {
      await closeWindow(RING_LABEL);
      await closeWindow(CARD_LABEL);
      return;
    }
    const common = { decorations: false, transparent: true, alwaysOnTop: true, skipTaskbar: true, shadow: false,
                     resizable: false, focus: false };
    await ensureWindow(RING_LABEL, { ...common, url: '/#guide-ring', title: 'Orion guide ring', fullscreen: true });
    const sw = typeof screen !== 'undefined' ? screen.availWidth : 1280;
    const sh = typeof screen !== 'undefined' ? screen.availHeight : 800;
    await ensureWindow(CARD_LABEL, { ...common, url: '/#guide-card', title: 'Orion guide', width: 380, height: 190,
                                     x: Math.max(0, sw - 400), y: Math.max(0, sh - 220) });
    const { emitTo } = await import('@tauri-apps/api/event');
    await emitTo(RING_LABEL, GUIDE_EVENT, state);
    await emitTo(CARD_LABEL, GUIDE_EVENT, state);
  } catch {
    /* older desktop app without the overlay permission: the HUD's own card still works */
  }
}

/** Inside the ring window: let every click fall through to the app underneath. */
export async function makeClickThrough(): Promise<void> {
  if (!isTauri()) return;
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  await getCurrentWindow().setIgnoreCursorEvents(true);
}

/** Inside an overlay window: follow the guide state the main window sends. */
export async function onGuideState(cb: (s: GuideState) => void): Promise<() => void> {
  if (!isTauri()) return () => {};
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  return getCurrentWindow().listen<GuideState>(GUIDE_EVENT, (e) => cb(e.payload));
}
