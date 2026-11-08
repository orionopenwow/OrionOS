/**
 * Where the black hole is on screen. App marks the element that holds the core with `data-orion-core`;
 * glass windows (lit edge, grow/spiral animations), the sky canvas (comets) and the taskbar read it here.
 */
export interface CoreGeometry {
  x: number;
  y: number;
  /** Radius of the painted disk in px (roughly where the bright part ends). */
  r: number;
}

export function coreGeometry(): CoreGeometry {
  const el = typeof document !== 'undefined' ? document.querySelector<HTMLElement>('[data-orion-core]') : null;
  if (el) {
    // Prefer the disk itself over the wrapper (the wrapper also holds the caption under the core).
    const disk = el.querySelector<HTMLElement>('[data-orion-disk], .singularity') ?? el;
    const b = disk.getBoundingClientRect();
    if (b.width > 0) return { x: b.left + b.width / 2, y: b.top + b.height / 2, r: b.width / 2 };
  }
  const w = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const h = typeof window !== 'undefined' ? window.innerHeight : 800;
  return { x: w / 2, y: h / 2, r: Math.min(w, h) * 0.2 };
}
