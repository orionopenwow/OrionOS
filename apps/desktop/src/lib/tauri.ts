/** Thin wrappers so the UI also runs in a plain browser (npm run dev:web). */
import type { UnlistenFn } from '@tauri-apps/api/event';

export const isTauri = (): boolean => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

export async function onTauriEvent(name: string, cb: () => void): Promise<UnlistenFn> {
  if (!isTauri()) return () => {};
  const { listen } = await import('@tauri-apps/api/event');
  return listen(name, cb);
}

export async function windowAction(action: 'minimize' | 'toggleMaximize' | 'close'): Promise<void> {
  if (!isTauri()) return;
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  const win = getCurrentWindow();
  if (action === 'minimize') await win.minimize();
  else if (action === 'toggleMaximize') await win.toggleMaximize();
  else await win.close();
}

export async function toggleAlwaysOnTop(enable: boolean): Promise<void> {
  if (!isTauri()) return;
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  const win = getCurrentWindow();
  await win.setAlwaysOnTop(enable);
}
