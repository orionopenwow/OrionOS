/**
 * A small tap on the daemon's event stream for code that lives outside React state (the black hole's smart
 * reactions). useOrionSocket forwards every event here; listeners must be cheap and never throw.
 */
export interface LooseDaemonEvent {
  event: string;
  [key: string]: unknown;
}

const listeners = new Set<(ev: LooseDaemonEvent) => void>();

export function emitDaemonEvent(ev: LooseDaemonEvent) {
  for (const l of listeners) {
    try {
      l(ev);
    } catch (err) {
      console.warn('[daemon events] listener failed', err);
    }
  }
}

export function onDaemonEvent(cb: (ev: LooseDaemonEvent) => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
