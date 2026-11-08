/**
 * HUD window manager: which of Orion's own glass windows are open, which one has focus (it gets the violet
 * edge glow and sits on top) and which are minimised (spiralled into the disk, still on the taskbar).
 * Open/closed state itself stays with the owner of each window (App / FloatingWidgetsLayer); this store
 * only tracks stacking order, focus and minimised windows.
 */
import { useSyncExternalStore } from 'react';

export type ExitKind = 'close' | 'minimise';

interface Snapshot {
  /** Mounted windows, back to front. The last non-minimised one has focus. */
  order: string[];
  minimised: Record<string, true>;
  maximised: Record<string, true>;
}

let snap: Snapshot = { order: [], minimised: {}, maximised: {} };
const exitKinds: Record<string, ExitKind> = {};
const listeners = new Set<() => void>();

function set(next: Partial<Snapshot>) {
  snap = { ...snap, ...next };
  listeners.forEach((l) => l());
}

function without<T extends Record<string, true>>(rec: T, id: string): T {
  if (!rec[id]) return rec;
  const copy = { ...rec };
  delete copy[id];
  return copy;
}

export const windowManager = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => snap,
  /** A window appeared (mounted): it comes to the front. */
  opened(id: string) {
    delete exitKinds[id];
    set({ order: [...snap.order.filter((x) => x !== id), id] });
  },
  /** A window finished leaving the screen. Minimised windows keep their taskbar flag. */
  gone(id: string) {
    set({ order: snap.order.filter((x) => x !== id) });
  },
  focus(id: string) {
    if (snap.order[snap.order.length - 1] === id && !snap.minimised[id]) return;
    set({ order: [...snap.order.filter((x) => x !== id), id], minimised: without(snap.minimised, id) });
  },
  minimise(id: string) {
    exitKinds[id] = 'minimise';
    set({ minimised: { ...snap.minimised, [id]: true } });
  },
  restore(id: string) {
    delete exitKinds[id];
    if (!snap.minimised[id]) return;
    set({ minimised: without(snap.minimised, id) });
  },
  /** Closing clears the minimised flag so the taskbar tile goes back to "not open". */
  closed(id: string) {
    exitKinds[id] = 'close';
    set({ minimised: without(snap.minimised, id), maximised: without(snap.maximised, id) });
  },
  toggleMaximise(id: string) {
    set({ maximised: snap.maximised[id] ? without(snap.maximised, id) : { ...snap.maximised, [id]: true } });
  },
  exitKind: (id: string): ExitKind => exitKinds[id] ?? 'close',
  isFocused(id: string, s: Snapshot = snap) {
    for (let i = s.order.length - 1; i >= 0; i--) {
      const w = s.order[i];
      if (!s.minimised[w]) return w === id;
    }
    return false;
  },
  /** Stacking rank (0 = back) so the focused window is drawn on top. */
  rank: (id: string, s: Snapshot = snap) => Math.max(0, s.order.indexOf(id)),
};

export function useWindowManager(): Snapshot {
  return useSyncExternalStore(windowManager.subscribe, windowManager.get, windowManager.get);
}

/** Taskbar-style click: open it, bring it back, focus it, or (if it already has focus) minimise it. */
export function taskbarClick(id: string, open: boolean, setOpen: (v: boolean) => void) {
  const s = windowManager.get();
  if (!open) {
    windowManager.restore(id);
    setOpen(true);
  } else if (s.minimised[id]) windowManager.restore(id);
  else if (windowManager.isFocused(id)) windowManager.minimise(id);
  else windowManager.focus(id);
}
